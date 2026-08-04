"""验证 HumanEval 只能通过固定 Docker 边界执行并且结果不泄漏隐藏校验。"""

from __future__ import annotations

import gzip
import hashlib
import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from evalhub.adapters import StaticMappingAdapter
from evalhub.benchmarks import Capability, ExecutorKind, get_benchmark_spec
from evalhub.benchmarks.humaneval import (
    DockerHumanEvalSandbox,
    HumanEvalProblem,
    SandboxResult,
    load_humaneval_problems,
    run_humaneval_benchmark,
)
from evalhub.benchmarks.readiness import benchmark_readiness
from evalhub.datasets.hexagon_manifest import HexagonSampleSpec


def _digest(value: str) -> str:
    """返回测试清单字段使用的 UTF-8 SHA-256 十六进制摘要。

    Args:
        value: 需要固定到测试清单中的完整字符串。

    Returns:
        与生产清单协议一致的小写 SHA-256 摘要。
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _problem(*, test: str = "SECRET_HIDDEN_TEST") -> HumanEvalProblem:
    """构造单题 HumanEval 夹具，并允许隐藏测试携带泄漏探针。

    Args:
        test: 只允许发送到 Docker 标准输入的隐藏校验源码。

    Returns:
        包含固定提示、标准实现和入口点的不可变问题对象。
    """
    return HumanEvalProblem(
        sample_id="hexagon_humaneval_01",
        source_key="HumanEval/1",
        prompt="def one():\n",
        canonical_solution="    return 1\n",
        test=test,
        entry_point="one",
        input_zh="实现返回 1 的函数。",
    )


class FakeSandbox:
    """记录候选执行次数并返回固定沙箱判定，不在宿主执行任何源码。"""

    def __init__(self, result: SandboxResult) -> None:
        """保存每次调用都要返回的安全判定结果。

        Args:
            result: 模拟 Docker 验证器返回的通过或失败结果。
        """
        self.result = result
        self.calls: list[tuple[str, str]] = []

    def run(self, problem: HumanEvalProblem, completion: str) -> SandboxResult:
        """仅记录来源键和候选文本并返回固定判定，绝不执行候选。

        Args:
            problem: 当前选中的 HumanEval 问题。
            completion: 模型生成的一次代码补全。

        Returns:
            构造时配置的沙箱判定。
        """
        self.calls.append((problem.source_key, completion))
        return self.result


def test_docker_command_has_fixed_isolation_and_no_host_mount() -> None:
    """Docker 命令必须禁网、只读、降权、限额且不允许任何宿主挂载。"""
    command = DockerHumanEvalSandbox().command()

    # 字面参数是安全边界本身；缺少任一项都会让不可信代码获得额外宿主能力。
    assert "--network=none" in command
    assert "--read-only" in command
    assert "--cap-drop=ALL" in command
    assert "--security-opt=no-new-privileges" in command
    assert "--memory=256m" in command
    # 进程、CPU、临时目录和固定镜像共同限制单题可消耗的宿主资源。
    assert "--cpus=1" in command
    assert "--pids-limit=64" in command
    assert "/tmp:rw,noexec,nosuid,size=16m" in command
    assert command[-1] == "evalhub-humaneval:1.0.0"
    assert not any(item in {"-v", "--volume", "--mount"} for item in command)


def test_sandbox_sends_hidden_payload_only_to_fixed_docker_stdin() -> None:
    """宿主只应把四个执行字段写入固定容器 stdin，并接受最小通过对象。"""
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """记录 Docker 调用并返回唯一合法的通过响应。

        Args:
            command: 生产边界生成的固定 Docker 参数。
            **kwargs: ``subprocess.run`` 使用的标准输入与超时参数。

        Returns:
            模拟固定镜像成功完成的文本进程结果。
        """
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, '{"passed": true}\n', "")

    result = DockerHumanEvalSandbox(command_runner=runner).run(_problem(), "    return 1\n")

    assert result == SandboxResult(passed=True, reason=None)
    assert len(calls) == 1
    command, kwargs = calls[0]
    payload = json.loads(str(kwargs["input"]))
    # 标准实现只用于显式集成自测，模型候选验证不得把它发送给镜像或回显。
    assert payload == {
        "prompt": "def one():\n",
        "completion": "    return 1\n",
        "test": "SECRET_HIDDEN_TEST",
        "entry_point": "one",
    }
    assert command == DockerHumanEvalSandbox().command()
    assert kwargs["timeout"] == 10


@pytest.mark.parametrize(
    ("outcome", "expected_reason"),
    [
        (
            subprocess.CompletedProcess(["docker"], 1, "SECRET_HIDDEN_TEST", "traceback"),
            "sandbox_failed",
        ),
        (subprocess.CompletedProcess(["docker"], 0, "not-json", ""), "invalid_result"),
        (subprocess.CompletedProcess(["docker"], 0, "x" * 1025, ""), "invalid_result"),
        (subprocess.CompletedProcess(["docker"], 0, "\udcff", ""), "invalid_result"),
        (FileNotFoundError("SECRET_HIDDEN_TEST"), "executor_not_ready"),
        (subprocess.TimeoutExpired(["docker"], 10, output="SECRET_HIDDEN_TEST"), "timeout"),
        (UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid"), "invalid_result"),
    ],
)
def test_sandbox_failures_are_closed_and_do_not_echo_process_output(
    outcome: subprocess.CompletedProcess[str]
    | OSError
    | subprocess.TimeoutExpired
    | UnicodeError,
    expected_reason: str,
) -> None:
    """缺失、超时、非零、畸形或超长输出都必须变成固定失败原因且不泄漏正文。

    Args:
        outcome: 注入到宿主命令边界的进程结果或边界异常。
        expected_reason: 对应失败类别允许公开的固定短原因。
    """

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """返回或抛出参数化的 Docker 边界结果。

        Args:
            command: 生产边界生成的固定 Docker 参数。
            **kwargs: 为兼容命令执行协议接收但不读取的参数。

        Returns:
            参数化的已完成进程结果。

        Raises:
            OSError: 模拟 Docker 命令缺失。
            subprocess.TimeoutExpired: 模拟容器超过宿主硬超时。
            UnicodeError: 模拟 Docker 输出无法按文本边界解码。
        """
        del command, kwargs
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    result = DockerHumanEvalSandbox(command_runner=runner).run(_problem(), "    return 1\n")

    assert result == SandboxResult(passed=False, reason=expected_reason)
    assert "SECRET_HIDDEN_TEST" not in json.dumps(result.__dict__)
    assert len(result.reason or "") <= 32


def test_sandbox_rejects_unexpected_verifier_fields() -> None:
    """即使容器返回通过，附带源码等额外字段的对象也必须整体拒绝。"""

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """返回伪装成通过但携带隐藏内容的镜像响应。

        Args:
            command: 生产边界生成的固定 Docker 参数。
            **kwargs: 为兼容命令执行协议接收但不读取的参数。

        Returns:
            带未授权字段的零退出码进程结果。
        """
        del kwargs
        return subprocess.CompletedProcess(
            command,
            0,
            '{"passed": true, "source": "SECRET_HIDDEN_TEST"}',
            "",
        )

    result = DockerHumanEvalSandbox(command_runner=runner).run(_problem(), "    return 1\n")

    assert result == SandboxResult(passed=False, reason="invalid_result")


def test_sandbox_rejects_non_string_failure_reason_without_raising() -> None:
    """验证器失败原因即使是不可哈希对象，宿主也必须安全拒绝而不是传播解析异常。"""

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """返回带数组原因的畸形失败对象。

        Args:
            command: 生产边界生成的固定 Docker 参数。
            **kwargs: 为兼容命令执行协议接收但不读取的参数。

        Returns:
            零退出码但原因类型不合法的进程结果。
        """
        del kwargs
        return subprocess.CompletedProcess(command, 0, '{"passed":false,"reason":[]}', "")

    result = DockerHumanEvalSandbox(command_runner=runner).run(_problem(), "    return 1\n")

    assert result == SandboxResult(passed=False, reason="invalid_result")


def test_runner_reports_pass_at_one_without_exposing_tests_or_solution() -> None:
    """Pass@1 摘要和样本回调可展示候选，但不得包含隐藏测试或标准实现。"""
    sandbox = FakeSandbox(SandboxResult(passed=True))
    progress: list[tuple[int, int]] = []
    emitted: list[dict[str, object]] = []

    result = run_humaneval_benchmark(
        job_id="job_1",
        adapter=StaticMappingAdapter({"def one():\n": "    return 1\n"}),
        problems=[_problem()],
        sandbox=sandbox,
        on_progress=lambda completed, total: progress.append((completed, total)),
        on_sample_result=lambda sample, completed, total: emitted.append(
            {**sample, "completed": completed, "total": total}
        ),
    )

    assert result["metric"] == "pass@1"
    assert result["passed_samples"] == 1
    assert progress == [(0, 1), (1, 1)]
    assert sandbox.calls == [("HumanEval/1", "    return 1\n")]
    # 对外参考文案固定为通过语义；序列化全结果可直接证明两类秘密均未进入结果树。
    assert emitted[0]["reference"] == "hidden tests passed"
    serialized = json.dumps({"result": result, "emitted": emitted}, ensure_ascii=False)
    assert "SECRET_HIDDEN_TEST" not in serialized
    assert "canonical_solution" not in serialized
    assert "    return 1\\n" in serialized


def test_runner_marks_failed_candidate_without_leaking_sandbox_details() -> None:
    """候选失败仍是已评测的零分样本，摘要只公开固定沙箱原因。"""
    sandbox = FakeSandbox(SandboxResult(passed=False, reason="verification_failed"))

    result = run_humaneval_benchmark(
        job_id="job_1",
        adapter=StaticMappingAdapter({"def one():\n": "    return 0\n"}),
        problems=[_problem()],
        sandbox=sandbox,
    )

    sample = result["sample_results"][0]
    assert result["passed_samples"] == 0
    assert result["failed_sample_ids"] == ["hexagon_humaneval_01"]
    assert sample["score"] == 0.0
    assert sample["reason"] == "verification_failed"


def test_runner_replaces_dynamic_sandbox_reason_before_emitting_result() -> None:
    """替代沙箱返回的动态错误文本不得绕过 Docker 解析器进入持久化结果。"""
    sandbox = FakeSandbox(SandboxResult(passed=False, reason="SECRET_HIDDEN_TEST traceback"))

    result = run_humaneval_benchmark(
        job_id="job_1",
        adapter=StaticMappingAdapter({"def one():\n": "    return 0\n"}),
        problems=[_problem()],
        sandbox=sandbox,
    )

    serialized = json.dumps(result)
    assert "SECRET_HIDDEN_TEST" not in serialized
    assert result["sample_results"][0]["reason"] == "sandbox_failed"


def test_loader_keeps_only_manifest_selected_humaneval_ids_in_memory(tmp_path: Path) -> None:
    """gzip 加载器必须按清单只保留选中 ID，并复核英文提示与标准实现摘要。"""
    selected = {
        "task_id": "HumanEval/1",
        "prompt": "def one():\n",
        "canonical_solution": "    return 1\n",
        "test": "def check(candidate):\n    assert candidate() == 1\n",
        "entry_point": "one",
    }
    unselected = {
        "task_id": "HumanEval/2",
        "prompt": "def two():\n",
        "canonical_solution": "    return 2\n",
        "test": "SECRET_UNSELECTED_TEST",
        "entry_point": "two",
    }
    path = tmp_path / "HumanEval.jsonl.gz"
    # 直接写 gzip 夹具能证明生产加载没有先把归档解压为磁盘文件。
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        stream.write(json.dumps(unselected) + "\n")
        stream.write(json.dumps(selected) + "\n")
    spec = HexagonSampleSpec(
        id="hexagon_humaneval_01",
        benchmark_id="hexagon-humaneval",
        capability=Capability.CODING,
        source_key="HumanEval/1",
        selection_stratum="HumanEval/1",
        input_sha256=_digest(selected["prompt"]),
        reference_sha256=_digest(selected["canonical_solution"]),
        input_zh="实现返回 1 的函数。",
        reference_zh=None,
        input_zh_sha256=_digest("实现返回 1 的函数。"),
        reference_zh_sha256=None,
        translation_version="evalhub-zh-v1",
    )

    problems = load_humaneval_problems(path, manifest=(spec,))

    assert problems == [
        HumanEvalProblem(
            sample_id="hexagon_humaneval_01",
            source_key="HumanEval/1",
            prompt="def one():\n",
            canonical_solution="    return 1\n",
            test="def check(candidate):\n    assert candidate() == 1\n",
            entry_point="one",
            input_zh="实现返回 1 的函数。",
        )
    ]
    assert list(tmp_path.iterdir()) == [path]


def test_readiness_requires_docker_daemon_and_fixed_image() -> None:
    """HumanEval 就绪必须依次证明 Docker 服务和固定标签镜像都可访问。"""
    commands: list[list[str]] = []

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """记录 readiness 探测并把两条命令都模拟为成功。

        Args:
            command: readiness 要执行的 Docker 参数。
            **kwargs: 为兼容命令执行协议接收但不读取的参数。

        Returns:
            表示 Docker 服务或镜像存在的零退出码结果。
        """
        del kwargs
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "ok", "")

    readiness = benchmark_readiness(
        get_benchmark_spec("hexagon-humaneval"), command_runner=runner
    )

    assert readiness.ready is True
    assert readiness.code == "ready"
    assert commands[0][:2] == ["docker", "version"]
    assert commands[1] == ["docker", "image", "inspect", "evalhub-humaneval:1.0.0"]


def test_readiness_fails_closed_with_exact_build_command() -> None:
    """Docker 缺失或镜像不可见时必须保持未就绪并给出唯一构建命令。"""

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """让 Docker 服务探测成功、固定镜像探测失败。

        Args:
            command: readiness 要执行的 Docker 参数。
            **kwargs: 为兼容命令执行协议接收但不读取的参数。

        Returns:
            根据命令类型返回成功的服务探测或失败的镜像探测。
        """
        del kwargs
        return subprocess.CompletedProcess(command, 0 if command[1] == "version" else 1, "", "")

    readiness = benchmark_readiness(
        get_benchmark_spec("hexagon-humaneval"), command_runner=runner
    )

    assert readiness.ready is False
    assert readiness.code == "executor_not_ready"
    assert "./scripts/build_humaneval_image.sh" in readiness.message
    assert "SECRET" not in readiness.message


def test_readiness_preserves_native_and_unsupported_executor_boundaries() -> None:
    """共享 readiness 必须保持原生可用，并拒绝未接通的其他沙箱执行器。"""
    native = benchmark_readiness(get_benchmark_spec("gsm8k"))
    unsupported = benchmark_readiness(
        replace(
            get_benchmark_spec("hexagon-humaneval"),
            id="another-sandbox",
            executor=ExecutorKind.SANDBOXED_CODE,
        )
    )

    assert native.ready is True
    assert unsupported.ready is False
    assert unsupported.code == "executor_not_ready"
