"""只通过固定 Docker 镜像执行 Hexagon HumanEval Pass@1 评分。"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, Protocol

from evalhub.adapters.base import ModelAdapter

if TYPE_CHECKING:
    from evalhub.datasets.hexagon_manifest import HexagonSampleSpec

ProgressCallback = Callable[[int, int], None]
SampleDictCallback = Callable[[dict[str, object], int, int], None]
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
TokenFactory = Callable[[], str]

_IMAGE = "evalhub-humaneval:1.0.0"
_IMAGE_USER = "10001:10001"
_IMAGE_ENTRYPOINT = ["python", "/opt/evalhub/verify.py"]
_IMAGE_INSPECT_FORMAT = "{{.Id}}\t{{.Config.User}}\t{{json .Config.Entrypoint}}"
_IMAGE_ID_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
_CONTAINER_NAME_PATTERN = re.compile(r"evalhub-humaneval-[0-9a-f]{32}")
_MAX_PAYLOAD_BYTES = 1024 * 1024
_MAX_RESULT_BYTES = 1024
_VERIFICATION_FAILURE_REASONS = frozenset({"timeout", "verification_failed"})
_INFRASTRUCTURE_REASONS = frozenset({"invalid_payload", "execution_failed"})
_PUBLIC_FAILURE_REASONS = _VERIFICATION_FAILURE_REASONS | frozenset(
    {"executor_not_ready", "sandbox_failed", "invalid_result"}
)


@dataclass(frozen=True)
class HumanEvalProblem:
    """保存一条选中题目的模型提示和只允许发送给 Docker 的官方校验字段。"""

    sample_id: str
    source_key: str
    prompt: str
    canonical_solution: str
    test: str
    entry_point: str
    input_zh: str


@dataclass(frozen=True)
class SandboxResult:
    """保存 Docker 验证器唯一允许返回的通过状态和固定短原因。"""

    passed: bool
    reason: str | None = None


class SandboxInfrastructureError(RuntimeError):
    """表示当前样本无法获得可信判定，调用方必须中止而不是记零分。"""

    def __init__(self, code: str) -> None:
        """保存固定短状态码，异常文本不包含进程输出、源码或镜像动态内容。

        Args:
            code: 调用方可稳定识别和安全展示的基础设施状态码。
        """
        self.code = code
        super().__init__(code)


class HumanEvalSandbox(Protocol):
    """描述 HumanEval Runner 依赖的最小隔离执行接口。"""

    def run(self, problem: HumanEvalProblem, completion: str) -> SandboxResult:
        """在隔离边界验证一次候选，并返回不含源码的安全结果。"""


class DockerHumanEvalSandbox:
    """通过无网络、无宿主挂载的固定 Docker 镜像验证一个 HumanEval 候选。"""

    image = _IMAGE

    def __init__(
        self,
        *,
        command_runner: CommandRunner = subprocess.run,
        token_factory: TokenFactory | None = None,
    ) -> None:
        """注入宿主命令边界，生产环境缺省使用 ``subprocess.run``。

        Args:
            command_runner: 接收固定 argv、stdin 和超时参数的文本命令执行器。
            token_factory: 测试可注入的容器名随机令牌生成器；生产缺省使用 UUID4。
        """
        self._command_runner = command_runner
        self._token_factory = token_factory or _container_token

    def command(self, *, image_id: str, container_name: str) -> list[str]:
        """返回固定 Docker argv，以不可变镜像 ID 和 controller 名字运行容器。

        Args:
            image_id: 刚刚从固定本地标签检查并验证的不可变 SHA-256 镜像 ID。
            container_name: controller 生成且符合固定格式的随机容器名。

        Returns:
            不含 shell、宿主挂载、可变标签或调用方输入名称的 Docker 参数列表。

        Raises:
            SandboxInfrastructureError: ID 或名字不符合固定安全格式时抛出。
        """
        if _IMAGE_ID_PATTERN.fullmatch(image_id) is None:
            raise SandboxInfrastructureError("image_untrusted")
        if _CONTAINER_NAME_PATTERN.fullmatch(container_name) is None:
            raise SandboxInfrastructureError("sandbox_failed")
        return [
            "docker",
            "run",
            "--rm",
            "-i",
            "--pull=never",
            "--name",
            container_name,
            "--user",
            _IMAGE_USER,
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--memory=256m",
            "--cpus=1",
            "--pids-limit=64",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=16m",
            image_id,
        ]

    def run(self, problem: HumanEvalProblem, completion: str) -> SandboxResult:
        """检查本地镜像后执行单题；基础设施故障以类型化异常中止评测。

        Args:
            problem: 包含英文提示、隐藏测试和入口点的选中官方题目。
            completion: 模型针对该提示生成的唯一候选补全。

        Returns:
            仅包含真实隐藏测试通过或候选未通过的安全判定。

        Raises:
            SandboxInfrastructureError: 镜像、Docker、协议或宿主硬超时不可信时抛出。
        """
        image_id = resolve_humaneval_image(self._command_runner)
        try:
            payload = _sandbox_payload(problem, completion)
        except ValueError as exc:
            raise SandboxInfrastructureError("invalid_payload") from exc
        container_name = f"evalhub-humaneval-{self._token_factory()}"
        command = self.command(image_id=image_id, container_name=container_name)

        # 只有验证后的不可变 ID 会收到隐藏载荷，controller 是唯一宿主输出来源。
        try:
            completed = self._command_runner(
                command,
                input=payload,
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            self._raise_after_cleanup(container_name, "timeout", exc)
        except UnicodeError as exc:
            self._raise_after_cleanup(container_name, "invalid_result", exc)
        except OSError as exc:
            self._raise_after_cleanup(container_name, "executor_not_ready", exc)
        except subprocess.SubprocessError as exc:
            self._raise_after_cleanup(container_name, "sandbox_failed", exc)
        if completed.returncode != 0:
            self._raise_after_cleanup(container_name, "sandbox_failed")
        try:
            return _parse_sandbox_result(completed.stdout)
        except SandboxInfrastructureError as exc:
            self._raise_after_cleanup(container_name, exc.code, exc)

    def _raise_after_cleanup(
        self,
        container_name: str,
        code: str,
        cause: BaseException | None = None,
    ) -> NoReturn:
        """确认异常容器已消失后抛出原始固定故障码，清理不可证实时升级。

        Args:
            container_name: 当前异常 Docker run 使用的 controller 生成名称。
            code: 清理确认成功后应向调用方报告的原始固定状态码。
            cause: 可选的宿主异常，仅作为内部因果链且不会进入异常文本。

        Raises:
            SandboxInfrastructureError: 始终抛出原始故障或更高优先级的清理故障。
        """
        self._cleanup_container(container_name)
        error = SandboxInfrastructureError(code)
        if cause is not None:
            raise error from cause
        raise error

    def _cleanup_container(self, container_name: str) -> None:
        """kill/rm 异常命名容器，并确认 daemon 可达且容器确实不存在。

        Args:
            container_name: 本次异常运行前由 controller 生成并写入 ``--name`` 的名字。

        Raises:
            SandboxInfrastructureError: daemon 不可达、检查失败或容器仍存在时抛出。
        """
        commands = (
            ["docker", "kill", container_name],
            ["docker", "rm", "-f", container_name],
        )
        # 两步都必须尝试；返回码稍后由独立 daemon 与容器存在性探测消除歧义。
        for command in commands:
            try:
                self._command_runner(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError, UnicodeError):
                continue

        # daemon 必须在短超时内响应，避免把后续 CLI 故障误判为“容器不存在”。
        try:
            daemon = self._command_runner(
                ["docker", "version", "--format", "{{.Server.Version}}"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
            raise SandboxInfrastructureError("cleanup_failed") from exc
        if daemon.returncode != 0:
            raise SandboxInfrastructureError("cleanup_failed")

        # 名字过滤会覆盖运行中和已退出容器；只有查询成功且完全空输出才是不存在的正证据。
        try:
            remaining = self._command_runner(
                [
                    "docker",
                    "container",
                    "ls",
                    "--all",
                    "--quiet",
                    "--filter",
                    f"name={container_name}",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
            raise SandboxInfrastructureError("cleanup_failed") from exc
        if remaining.returncode != 0 or remaining.stdout != "":
            raise SandboxInfrastructureError("cleanup_failed")


def _container_token() -> str:
    """生成只由宿主 controller 控制的随机容器名令牌。

    Returns:
        UUID4 的 32 位小写十六进制表示，不含 shell 或 Docker 名称分隔字符。
    """
    return uuid.uuid4().hex


def resolve_humaneval_image(command_runner: CommandRunner = subprocess.run) -> str:
    """检查固定本地标签的用户、入口点和 ID，并返回不可变镜像 ID。

    Args:
        command_runner: 用于只读 ``docker image inspect`` 的可替换命令边界。

    Returns:
        与固定标签当前指向一致、格式合法的 ``sha256:...`` 镜像 ID。

    Raises:
        SandboxInfrastructureError: Docker 不可用、标签缺失或配置不符合固定边界时抛出。
    """
    command = [
        "docker",
        "image",
        "inspect",
        "--format",
        _IMAGE_INSPECT_FORMAT,
        _IMAGE,
    ]
    try:
        completed = command_runner(
            command,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except UnicodeError as exc:
        raise SandboxInfrastructureError("image_untrusted") from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SandboxInfrastructureError("executor_not_ready") from exc
    if completed.returncode != 0:
        raise SandboxInfrastructureError("executor_not_ready")

    # 元数据协议同样有界且严格；任何额外行或字段都拒绝后再接触隐藏测试。
    try:
        if len(completed.stdout.encode("utf-8")) > _MAX_RESULT_BYTES:
            raise ValueError("image metadata is too large")
        image_id, user, entrypoint_raw = completed.stdout.rstrip("\n").split("\t")
        entrypoint = json.loads(entrypoint_raw)
    except (AttributeError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise SandboxInfrastructureError("image_untrusted") from exc
    if _IMAGE_ID_PATTERN.fullmatch(image_id) is None or user != _IMAGE_USER:
        raise SandboxInfrastructureError("image_untrusted")
    if entrypoint != _IMAGE_ENTRYPOINT:
        raise SandboxInfrastructureError("image_untrusted")
    return image_id


def load_humaneval_problems(
    path: Path,
    *,
    manifest: tuple[HexagonSampleSpec, ...] | None = None,
) -> list[HumanEvalProblem]:
    """按冻结清单顺序从 gzip 加载十条 HumanEval 问题，并复核英文摘要。

    Args:
        path: 已通过固定来源摘要校验的 HumanEval gzip JSONL 文件。
        manifest: 测试可注入的清单；缺省使用随包发布的完整 Hexagon v1 清单。

    Returns:
        只包含清单选中 ID 的问题列表；不会在磁盘产生解压文件。

    Raises:
        ValueError: 选择器缺失、重复，或英文提示与标准实现偏离冻结摘要时抛出。
        OSError: gzip 文件不可读时保留底层文件系统错误。
    """
    # 数据集模块本身依赖 Benchmark 模型；延迟导入避免包级公开导出形成循环初始化。
    from evalhub.datasets.hexagon_manifest import hexagon_manifest
    from evalhub.datasets.hexagon_sources import (
        hexagon_source_specs,
        load_selected_humaneval_rows,
    )

    frozen = manifest if manifest is not None else hexagon_manifest()
    expected_sha256 = (
        None if manifest is not None else hexagon_source_specs()["hexagon-humaneval"].sha256
    )
    selected = [item for item in frozen if item.benchmark_id == "hexagon-humaneval"]
    keys = [item.source_key for item in selected]
    rows = load_selected_humaneval_rows(path, keys, expected_sha256=expected_sha256)

    # 清单顺序是固定评测协议的一部分，来源文件的原始排列不能改变模型调用顺序。
    problems: list[HumanEvalProblem] = []
    for item in selected:
        row = rows[item.source_key]
        if hashlib.sha256(row.prompt.encode("utf-8")).hexdigest() != item.input_sha256:
            raise ValueError(f"input SHA-256 mismatch for {item.source_key}")
        if (
            hashlib.sha256(row.canonical_solution.encode("utf-8")).hexdigest()
            != item.reference_sha256
        ):
            raise ValueError(f"reference SHA-256 mismatch for {item.source_key}")
        # 标准实现与隐藏测试只进入专用问题对象，结果字典永远不序列化该对象。
        problems.append(
            HumanEvalProblem(
                sample_id=item.id,
                source_key=item.source_key,
                prompt=row.prompt,
                canonical_solution=row.canonical_solution,
                test=row.test,
                entry_point=row.entry_point,
                input_zh=item.input_zh,
            )
        )
    return problems


def run_humaneval_benchmark(
    *,
    job_id: str,
    adapter: ModelAdapter,
    problems: list[HumanEvalProblem],
    sandbox: HumanEvalSandbox,
    skip_sample_ids: frozenset[str] = frozenset(),
    on_progress: ProgressCallback | None = None,
    on_sample_result: SampleDictCallback | None = None,
) -> dict[str, object]:
    """为每题生成一次代码并在 Docker 中评分，返回 Pass@1 兼容摘要。

    Args:
        job_id: 调度层创建的稳定任务标识。
        adapter: 只接收官方英文提示的模型生成边界。
        problems: 按冻结清单顺序加载的 HumanEval 问题。
        sandbox: 唯一获准执行候选和隐藏测试的 Docker 边界。
        skip_sample_ids: 恢复执行时已经持久化、无需再次生成的样本标识。
        on_progress: 接收已完成数量和固定总题数的可选回调。
        on_sample_result: 每条新样本判定完成后接收脱敏结果的可选回调。

    Returns:
        包含本轮增量属性、评测/跳过计数、Pass@1 和脱敏样本结果的字典。

    Raises:
        ValueError: 问题样本 ID 重复，无法安全支持断点恢复时抛出。
    """
    sample_ids = [problem.sample_id for problem in problems]
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError("duplicate HumanEval sample IDs")
    skipped_samples = sum(sample_id in skip_sample_ids for sample_id in sample_ids)
    completed = skipped_samples
    if on_progress is not None:
        on_progress(completed, len(problems))
    sample_results: list[dict[str, object]] = []

    # 每题只生成一个确定性候选；中文翻译和官方答案都不会进入模型调用参数。
    for problem in problems:
        if problem.sample_id in skip_sample_ids:
            continue
        completion = adapter.generate(problem.prompt, temperature=0, num_predict=256)
        verdict = sandbox.run(problem, completion)
        sample_result = _sample_result(problem, completion, verdict)
        sample_results.append(sample_result)
        completed += 1
        if on_sample_result is not None:
            on_sample_result(sample_result, completed, len(problems))
        if on_progress is not None:
            on_progress(completed, len(problems))

    passed_samples = sum(float(item["score"]) >= 1.0 for item in sample_results)
    failed_ids = [
        str(item["sample_id"]) for item in sample_results if float(item["score"]) < 1.0
    ]
    # 汇总只引用脱敏样本字典，标准实现和隐藏测试没有任何可序列化入口。
    return {
        "job_id": job_id,
        "status": "success",
        "dataset": "hexagon-humaneval",
        "benchmark": "Hexagon · HumanEval",
        "metric": "pass@1",
        "incremental": skipped_samples > 0,
        "total_samples": len(problems),
        "evaluated_samples": len(sample_results),
        "skipped_samples": skipped_samples,
        "passed_samples": passed_samples,
        "average_score": round(passed_samples / len(sample_results), 4)
        if sample_results
        else None,
        "failed_sample_ids": failed_ids,
        "failed_examples": [item for item in sample_results if float(item["score"]) < 1.0][:5],
        "sample_results": sample_results,
    }


def _sandbox_payload(problem: HumanEvalProblem, completion: str) -> str:
    """序列化固定四字段容器输入，并拒绝会放大管道内存的超长载荷。

    Args:
        problem: 包含提示、隐藏测试和入口点的官方问题。
        completion: 模型生成的唯一候选补全。

    Returns:
        紧凑且以换行结尾的 JSON 文本。

    Raises:
        ValueError: 载荷超过固定一 MiB 上限时抛出。
    """
    payload = json.dumps(
        {
            "prompt": problem.prompt,
            "completion": completion,
            "test": problem.test,
            "entry_point": problem.entry_point,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if len(payload.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        raise ValueError("HumanEval sandbox payload is too large")
    return f"{payload}\n"


def _parse_sandbox_result(output: str) -> SandboxResult:
    """严格解析有界验证器输出，只把真实候选失败转换为正常判定。

    Args:
        output: Docker 进程捕获的完整标准输出。

    Returns:
        合法最小通过对象或真实候选失败对象对应的判定。

    Raises:
        SandboxInfrastructureError: 输出畸形，或 controller 报告自身故障时抛出。
    """
    if not isinstance(output, str):
        raise SandboxInfrastructureError("invalid_result")
    try:
        if len(output.encode("utf-8")) > _MAX_RESULT_BYTES:
            raise SandboxInfrastructureError("invalid_result")
    except UnicodeError as exc:
        raise SandboxInfrastructureError("invalid_result") from exc
    try:
        payload = json.loads(output)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise SandboxInfrastructureError("invalid_result") from exc
    if not isinstance(payload, dict) or type(payload.get("passed")) is not bool:
        raise SandboxInfrastructureError("invalid_result")

    # 通过对象只能有一个字段；失败对象只能携带白名单短原因，禁止任意诊断回流宿主。
    if payload["passed"] is True:
        if set(payload) != {"passed"}:
            raise SandboxInfrastructureError("invalid_result")
        return SandboxResult(True)
    reason = payload.get("reason")
    if set(payload) != {"passed", "reason"} or not isinstance(reason, str):
        raise SandboxInfrastructureError("invalid_result")
    if reason in _INFRASTRUCTURE_REASONS:
        raise SandboxInfrastructureError(reason)
    if reason not in _VERIFICATION_FAILURE_REASONS:
        raise SandboxInfrastructureError("invalid_result")
    return SandboxResult(False, reason)


def _sample_result(
    problem: HumanEvalProblem, completion: str, verdict: SandboxResult
) -> dict[str, object]:
    """把隔离判定转换为可持久化结果，并用固定参考文案替代隐藏测试。

    Args:
        problem: 当前题目的公开提示、来源键和中文展示翻译。
        completion: 模型生成且允许展示的一次候选补全。
        verdict: Docker 返回的脱敏通过状态。

    Returns:
        不包含 ``test`` 或 ``canonical_solution`` 的 JSON 兼容样本结果。
    """
    reason = verdict.reason if verdict.reason in _PUBLIC_FAILURE_REASONS else "sandbox_failed"
    return {
        "sample_id": problem.sample_id,
        "input": problem.prompt,
        "prediction": completion,
        "reference": "hidden tests passed",
        "metric": "pass@1",
        "score": 1.0 if verdict.passed else 0.0,
        "reason": None if verdict.passed else reason,
        "metadata": {"source_key": problem.source_key, "input_zh": problem.input_zh},
    }
