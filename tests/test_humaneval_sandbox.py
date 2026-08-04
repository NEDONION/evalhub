"""验证 HumanEval 只能通过固定 Docker 边界执行并且结果不泄漏隐藏校验。"""

from __future__ import annotations

import errno
import gzip
import hashlib
import importlib.util
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import pytest

from evalhub.adapters import StaticMappingAdapter
from evalhub.benchmarks import Capability, ExecutorKind, get_benchmark_spec
from evalhub.benchmarks.humaneval import (
    DockerHumanEvalSandbox,
    HumanEvalProblem,
    SandboxInfrastructureError,
    SandboxResult,
    load_humaneval_problems,
    run_humaneval_benchmark,
)
from evalhub.benchmarks.readiness import benchmark_readiness
from evalhub.datasets.hexagon_manifest import HexagonSampleSpec

_IMAGE_ID = f"sha256:{'a' * 64}"
_CONTAINER_TOKEN = "b" * 32
_CONTAINER_NAME = f"evalhub-humaneval-{_CONTAINER_TOKEN}"
_CLEANUP_QUERY = [
    "docker",
    "container",
    "ls",
    "--all",
    "--quiet",
    "--filter",
    f"name={_CONTAINER_NAME}",
]
_IMAGE_INSPECT_OUTPUT = (
    f'{_IMAGE_ID}\t10001:10001\t["python","/opt/evalhub/verify.py"]\n'
)


def _confirmed_cleanup_result(command: list[str]) -> subprocess.CompletedProcess[str]:
    """模拟 kill/rm 已让命名容器消失，且 Docker daemon 仍可可靠查询。

    Args:
        command: 宿主清理阶段的 kill、rm、daemon version 或 container ls argv。

    Returns:
        kill/rm 可竞态失败，daemon 探测与最终空名字查询都以零状态成功。

    Raises:
        AssertionError: 调用方传入了清理确认协议之外的命令。
    """
    # kill/rm 的竞态返回码不承担不存在证明，必须继续走独立查询。
    if command[1] in {"kill", "rm"}:
        return subprocess.CompletedProcess(command, 1, "", "")
    # daemon 可达性与最终容器列表查询都必须由 CLI 成功返回。
    if command[1] == "version":
        return subprocess.CompletedProcess(command, 0, "26.1", "")
    if command[1:3] == ["container", "ls"]:
        assert command[3:6] == ["--all", "--quiet", "--filter"]
        assert command[6].startswith("name=evalhub-humaneval-")
        return subprocess.CompletedProcess(command, 0, "", "")
    # 未知命令说明生产清理协议发生了未经测试覆盖的改变。
    raise AssertionError(f"unexpected cleanup command: {command}")


def _load_verifier_controller() -> ModuleType:
    """只导入可信 verifier controller，测试不会调用或执行任何候选源码。

    Returns:
        从 Docker 构建上下文加载且未执行 ``main`` 的 controller 模块。

    Raises:
        RuntimeError: Python 无法为 verifier 文件创建导入规格时抛出。
    """
    path = Path("docker/hexagon-humaneval/verify.py")
    spec = importlib.util.spec_from_file_location("hexagon_humaneval_verify", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load HumanEval verifier controller")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_verifier_worker() -> ModuleType:
    """只导入候选 worker 的安全策略构造函数，不调用 ``main`` 或执行候选源码。

    Returns:
        从 Docker 构建上下文加载且尚未安装进程策略的 worker 模块。

    Raises:
        RuntimeError: Python 无法为 worker 文件创建导入规格时抛出。
    """
    path = Path("docker/hexagon-humaneval/worker.py")
    spec = importlib.util.spec_from_file_location("hexagon_humaneval_worker", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load HumanEval verifier worker")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
        prompt="def one():\n",
        canonical_solution="    return 1\n",
        test=test,
        entry_point="one",
        metadata={"source_key": "HumanEval/1", "input_zh": "实现返回 1 的函数。"},
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


def test_verifier_controller_uses_worker_only_as_candidate_rpc() -> None:
    """可信 controller 必须隔离候选调用，且 worker 输入不得包含隐藏测试。"""
    verifier = _load_verifier_controller()
    calls: list[tuple[list[str], dict[str, object]]] = []

    class FakeProcess:
        """模拟只返回候选函数值、无权返回最终 verdict 的隔离 worker。"""

        returncode = 0
        pid = 12345

        def communicate(self, input: bytes, timeout: int) -> tuple[None, None]:
            """记录有界 RPC 输入，并向容器内临时通道写入候选返回值。

            Args:
                input: controller 传给 worker stdin 的候选源码与调用参数。
                timeout: controller 对单次候选调用施加的秒级硬超时。

            Returns:
                输出写入独立临时文件，因此进程等待接口不返回动态文本。
            """
            calls.append((["communicate"], {"input": input, "timeout": timeout}))
            output = calls[0][1]["stdout"]
            output.write(b'{"ok":true,"value":3}\n')
            output.flush()
            return None, None

    def process_factory(command: list[str], **kwargs: object) -> FakeProcess:
        """记录 worker 创建参数并返回不执行任何源码的假进程。

        Args:
            command: controller 固定的 Python worker argv。
            **kwargs: stdin、容器内临时输出、会话和句柄继承配置。

        Returns:
            可由 controller 等待的假进程。
        """
        calls.append((command, kwargs))
        return FakeProcess()

    payload = {
        "prompt": "def add(a, b):\n",
        "completion": "    return a + b\n",
        "test": "SECRET_HIDDEN_TEST",
        "entry_point": "add",
    }
    killed_groups: list[int] = []
    result = verifier._run_candidate(
        payload,
        (1, 2),
        {},
        process_factory=process_factory,
        group_killer=lambda process_id: killed_groups.append(process_id),
    )

    command, kwargs = calls[0]
    assert command == [sys.executable, "/opt/evalhub/worker.py"]
    assert kwargs["stdin"] is subprocess.PIPE
    assert kwargs["stdout"] is not subprocess.PIPE
    assert kwargs["stdout"] is not sys.stdout
    assert kwargs["stderr"] is subprocess.DEVNULL
    assert kwargs["close_fds"] is True
    assert kwargs["start_new_session"] is True
    call_payload = json.loads(bytes(calls[1][1]["input"]))
    assert call_payload == {
        "prompt": "def add(a, b):\n",
        "completion": "    return a + b\n",
        "entry_point": "add",
        "args": [1, 2],
        "kwargs": {},
    }
    assert "SECRET_HIDDEN_TEST" not in bytes(calls[1][1]["input"]).decode()
    assert killed_groups == [12345]
    assert result == 3


def test_verifier_controller_owns_hidden_check_and_final_verdict() -> None:
    """隐藏 check 只能调用 RPC 代理，候选源码不能在 controller 内设置通过状态。"""
    verifier = _load_verifier_controller()
    payload = {
        "prompt": "def add(a, b):\n",
        "completion": "    import os\n    os._exit(73)\n",
        "test": "def check(candidate):\n    assert candidate(2, 3) == 5\n",
        "entry_point": "add",
    }
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def candidate_runner(
        received: dict[str, str], args: tuple[object, ...], kwargs: dict[str, object]
    ) -> int:
        """模拟 worker 仅按调用参数返回值，并确认它没有获得隐藏 check 源码。

        Args:
            received: controller 保存的完整载荷，只由受信任代理读取必要候选字段。
            args: 隐藏测试传给候选的位置参数。
            kwargs: 隐藏测试传给候选的关键字参数。

        Returns:
            与 ``add`` 合同一致的整数，供可信隐藏断言独立判断。
        """
        assert received["test"] == payload["test"]
        calls.append((args, kwargs))
        return int(args[0]) + int(args[1])

    result = verifier._verify(payload, candidate_runner=candidate_runner)

    assert result == {"passed": True}
    assert calls == [((2, 3), {})]


def test_verifier_controller_disables_ptrace_access_before_spawning_worker() -> None:
    """可信 controller 必须设为不可转储，阻止同 UID worker 打开其 proc 文件描述符。"""
    verifier = _load_verifier_controller()
    calls: list[tuple[int, int, int, int, int]] = []

    def prctl(option: int, value: int, arg3: int, arg4: int, arg5: int) -> int:
        """记录 Linux prctl 参数并模拟成功。

        Args:
            option: 要设置的进程安全属性。
            value: 属性的新整数值。
            arg3: 当前操作未使用的第三参数。
            arg4: 当前操作未使用的第四参数。
            arg5: 当前操作未使用的第五参数。

        Returns:
            零表示内核接受安全设置。
        """
        calls.append((option, value, arg3, arg4, arg5))
        return 0

    verifier._lock_controller_process(prctl=prctl)

    assert calls == [(4, 0, 0, 0, 0)]


def test_verifier_controller_classifies_worker_cleanup_failure_as_infrastructure() -> None:
    """controller 无法回收候选进程组时必须报告基础设施故障，不能记作断言失败。"""
    verifier = _load_verifier_controller()

    class FakeProcess:
        """模拟已经正常写出候选返回值、但进程组清理被内核拒绝的 worker。"""

        returncode = 0
        pid = 12345

        def __init__(self, output: object) -> None:
            """保存 controller 创建的容器内临时输出通道。

            Args:
                output: ``_run_candidate`` 传给 worker stdout 的二进制临时文件。
            """
            self.output = output

        def communicate(self, input: bytes, timeout: int) -> tuple[None, None]:
            """写入一个合法候选值并模拟 worker 正常结束。

            Args:
                input: 本测试无需解析的候选 RPC 字节。
                timeout: controller 设置的单次调用硬超时。

            Returns:
                动态返回值已写入独立文件，因此等待接口没有管道输出。
            """
            del input, timeout
            self.output.write(b'{"ok":true,"value":1}')
            self.output.flush()
            return None, None

    def process_factory(command: list[str], **kwargs: object) -> FakeProcess:
        """返回绑定到临时输出通道的假 worker。

        Args:
            command: 固定 Python worker argv，本测试不执行它。
            **kwargs: 包含 controller 创建的 stdout 临时通道。

        Returns:
            可由 controller 等待的正常假进程。
        """
        del command
        return FakeProcess(kwargs["stdout"])

    def deny_cleanup(process_id: int) -> None:
        """模拟内核拒绝 controller 终止指定 worker 进程组。

        Args:
            process_id: 待清理 worker 的固定假 PID。

        Raises:
            PermissionError: 每次调用均表示进程回收基础设施失败。
        """
        del process_id
        raise PermissionError("SECRET_KERNEL_DETAIL")

    payload = {
        "prompt": "def one():\n",
        "completion": "    return 1\n",
        "test": "SECRET_HIDDEN_TEST",
        "entry_point": "one",
    }
    with pytest.raises(verifier.ControllerInfrastructureError) as raised:
        verifier._run_candidate(
            payload,
            (),
            {},
            process_factory=process_factory,
            group_killer=deny_cleanup,
        )

    assert "SECRET_KERNEL_DETAIL" not in str(raised.value)


@pytest.mark.parametrize(
    ("machine", "expected_arch"),
    [("x86_64", 0xC000003E), ("aarch64", 0xC00000B7)],
)
def test_worker_seccomp_filter_blocks_signal_process_and_session_syscalls(
    machine: str, expected_arch: int
) -> None:
    """两种镜像架构都必须先校验 arch，再以 EPERM 拒绝进程、信号和会话逃逸。

    Args:
        machine: 固定镜像允许运行的 Linux 机器架构名称。
        expected_arch: seccomp_data 中必须匹配的 Linux audit arch 常量。
    """
    worker = _load_verifier_worker()
    arch, syscalls = worker._seccomp_policy(machine)
    required = {
        "kill",
        "tkill",
        "tgkill",
        "pidfd_send_signal",
        "fork",
        "vfork",
        "clone",
        "clone3",
        "setsid",
        "setpgid",
    }
    instructions = worker._seccomp_instructions(machine)

    assert arch == expected_arch
    assert required <= set(syscalls)
    assert instructions[:3] == (
        (0x20, 0, 0, 4),
        (0x15, 1, 0, expected_arch),
        (0x06, 0, 0, 0x80000000),
    )
    if machine == "x86_64":
        assert instructions[4:6] == (
            (0x35, 0, 1, 0x40000000),
            (0x06, 0, 0, 0x00050000 | errno.EPERM),
        )
    # 每个实际 syscall 编号后都必须紧邻 EPERM 返回；最后一条才允许其他系统调用。
    for syscall_number in set(syscalls.values()):
        index = instructions.index((0x15, 0, 1, syscall_number))
        assert instructions[index + 1] == (0x06, 0, 0, 0x00050000 | errno.EPERM)
    assert instructions[-1] == (0x06, 0, 0, 0x7FFF0000)


@pytest.mark.parametrize(
    ("machine", "expected_syscalls"),
    [
        (
            "x86_64",
            {
                # 异步 I/O 所有者与 socket 建立路径。
                "ioctl": 16,
                "socket": 41,
                "socketpair": 53,
                "fcntl": 72,
                # 同 UID 调度和优先级修改路径。
                "setpriority": 141,
                "sched_setparam": 142,
                "sched_setscheduler": 144,
                "sched_setaffinity": 203,
                "ioprio_set": 251,
                # NUMA 内存放置与父进程硬限制修改路径。
                "migrate_pages": 256,
                "move_pages": 279,
                "prlimit64": 302,
                # 跨进程内存与新式调度接口。
                "process_vm_readv": 310,
                "process_vm_writev": 311,
                "sched_setattr": 314,
                "pidfd_open": 434,
                # pidfd 和 process_* 的其余跨进程资源能力。
                "pidfd_getfd": 438,
                "process_madvise": 440,
                "process_mrelease": 448,
            },
        ),
        (
            "aarch64",
            {
                # 异步 I/O 与 I/O 优先级修改路径。
                "fcntl": 25,
                "ioctl": 29,
                "ioprio_set": 30,
                # 同 UID 调度与普通优先级修改路径。
                "sched_setparam": 118,
                "sched_setscheduler": 119,
                "sched_setaffinity": 122,
                "setpriority": 140,
                # socket 建立和父进程硬限制修改路径。
                "socket": 198,
                "socketpair": 199,
                # NUMA 内存放置与父进程硬限制修改路径。
                "migrate_pages": 238,
                "move_pages": 239,
                "prlimit64": 261,
                # 跨进程内存与新式调度接口。
                "process_vm_readv": 270,
                "process_vm_writev": 271,
                "sched_setattr": 274,
                "pidfd_open": 434,
                # pidfd 和 process_* 的其余跨进程资源能力。
                "pidfd_getfd": 438,
                "process_madvise": 440,
                "process_mrelease": 448,
            },
        ),
    ],
)
def test_worker_seccomp_filter_blocks_indirect_signal_and_resource_mutation_syscalls(
    machine: str, expected_syscalls: dict[str, int]
) -> None:
    """候选不得借异步 I/O 或同 UID 跨进程接口影响可信 controller。

    Args:
        machine: 固定镜像允许运行的 Linux 机器架构名称。
        expected_syscalls: 按 Linux 官方 syscall 表手工核对的拒绝项与编号。
    """
    worker = _load_verifier_worker()
    _, syscalls = worker._seccomp_policy(machine)
    instructions = worker._seccomp_instructions(machine)

    # 名称和编号必须同时匹配，避免存在拒绝项但落到错误架构编号而形成假保护。
    for name, syscall_number in expected_syscalls.items():
        assert syscalls[name] == syscall_number
        index = instructions.index((0x15, 0, 1, syscall_number))
        assert instructions[index + 1] == (0x06, 0, 0, 0x00050000 | errno.EPERM)

    # 新增拒绝项不能移除既有 x32 整段保护或把默认尾部改成隐式成功。
    if machine == "x86_64":
        assert instructions[4:6] == (
            (0x35, 0, 1, 0x40000000),
            (0x06, 0, 0, 0x00050000 | errno.EPERM),
        )
    assert instructions[-1] == (0x06, 0, 0, 0x7FFF0000)


def test_worker_seccomp_policy_fails_closed_on_unsupported_architecture() -> None:
    """未知 Linux 架构不得退化为无过滤候选执行。"""
    worker = _load_verifier_worker()

    with pytest.raises(OSError, match="unsupported worker architecture"):
        worker._seccomp_policy("riscv64")


def test_worker_seccomp_installation_failure_is_fatal() -> None:
    """no-new-privileges 成功但 seccomp 安装失败时 worker 必须得到可失败关闭的 OSError。"""
    worker = _load_verifier_worker()
    calls: list[tuple[object, ...]] = []

    def prctl(*args: object) -> int:
        """记录两个 prctl 阶段，并只拒绝第二个 seccomp filter 安装。

        Args:
            *args: worker 传给 Linux prctl 的固定操作和参数。

        Returns:
            第一次返回零，第二次返回负一以模拟内核拒绝过滤器。
        """
        calls.append(args)
        return 0 if len(calls) == 1 else -1

    with pytest.raises(OSError, match="cannot install worker seccomp policy"):
        worker._install_seccomp_policy(
            machine="x86_64",
            prctl=prctl,
            errno_reader=lambda: errno.EPERM,
        )

    assert calls[0] == (38, 1, 0, 0, 0)
    assert calls[1][0:2] == (22, 2)
    assert getattr(calls[1][2], "_obj", None) is not None


def test_worker_resource_limits_prevent_new_processes_after_spawn() -> None:
    """worker 启动后必须把文件大小和用户进程数硬限制降到不可恢复的固定值。"""
    worker = _load_verifier_worker()
    calls: list[tuple[int, tuple[int, int]]] = []

    def limit_setter(resource_id: int, limits: tuple[int, int]) -> None:
        """记录 worker 安装的资源硬限制，不修改测试进程本身。

        Args:
            resource_id: Python ``resource`` 模块的限制类型常量。
            limits: 同时写入的软限制和硬限制。
        """
        calls.append((resource_id, limits))

    worker._limit_worker_resources(limit_setter=limit_setter)

    assert calls == [
        (worker.resource.RLIMIT_FSIZE, (64 * 1024, 64 * 1024)),
        (worker.resource.RLIMIT_NPROC, (1, 1)),
    ]


def test_docker_command_has_fixed_isolation_and_no_host_mount() -> None:
    """Docker 命令必须禁网、只读、降权、限额且不允许任何宿主挂载。"""
    command = DockerHumanEvalSandbox().command(
        image_id=_IMAGE_ID, container_name=_CONTAINER_NAME
    )

    # 字面参数是安全边界本身；缺少任一项都会让不可信代码获得额外宿主能力。
    assert "--network=none" in command
    assert "--read-only" in command
    assert "--cap-drop=ALL" in command
    assert "--security-opt=no-new-privileges" in command
    assert "--memory=256m" in command
    # 进程、CPU、临时目录和固定镜像共同限制单题可消耗的宿主资源。
    assert "--cpus=1" in command
    assert "--pids-limit=64" in command
    assert "--pull=never" in command
    assert command[command.index("--user") + 1] == "10001:10001"
    assert command[command.index("--name") + 1] == _CONTAINER_NAME
    assert "/tmp:rw,noexec,nosuid,size=16m" in command
    assert command[-1] == _IMAGE_ID
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
        if command[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, _IMAGE_INSPECT_OUTPUT, "")
        return subprocess.CompletedProcess(command, 0, '{"passed": true}\n', "")

    sandbox = DockerHumanEvalSandbox(
        command_runner=runner, token_factory=lambda: _CONTAINER_TOKEN
    )
    result = sandbox.run(_problem(), "    return 1\n")

    assert result == SandboxResult(passed=True, reason=None)
    assert len(calls) == 2
    inspect_command, inspect_kwargs = calls[0]
    assert inspect_command[-1] == "evalhub-humaneval:1.0.0"
    assert "{{.Id}}" in str(inspect_kwargs.get("input", "")) or "--format" in inspect_command
    command, kwargs = calls[1]
    payload = json.loads(str(kwargs["input"]))
    # 标准实现只用于显式集成自测，模型候选验证不得把它发送给镜像或回显。
    assert payload == {
        "prompt": "def one():\n",
        "completion": "    return 1\n",
        "test": "SECRET_HIDDEN_TEST",
        "entry_point": "one",
    }
    assert command == sandbox.command(image_id=_IMAGE_ID, container_name=_CONTAINER_NAME)
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
    """Docker 或协议故障必须抛出脱敏基础设施异常，不能被记作样本零分。

    Args:
        outcome: 注入到宿主命令边界的进程结果或边界异常。
        expected_reason: 对应基础设施异常允许公开的固定短状态码。
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
        del kwargs
        if command[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, _IMAGE_INSPECT_OUTPUT, "")
        if command[1] in {"kill", "rm", "version"} or command[1:3] == [
            "container",
            "ls",
        ]:
            return _confirmed_cleanup_result(command)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    sandbox = DockerHumanEvalSandbox(
        command_runner=runner, token_factory=lambda: _CONTAINER_TOKEN
    )
    with pytest.raises(SandboxInfrastructureError) as raised:
        sandbox.run(_problem(), "    return 1\n")

    assert raised.value.code == expected_reason
    assert "SECRET_HIDDEN_TEST" not in str(raised.value)
    assert len(str(raised.value)) <= 32


def test_sandbox_timeout_kills_removes_and_confirms_named_container_absent() -> None:
    """宿主硬超时必须 kill/rm，并在 daemon 可达时确认命名容器已经不存在。"""
    calls: list[list[str]] = []

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """模拟容器超时并记录后续清理与可达性确认命令。

        Args:
            command: 镜像探测、容器执行或固定名字清理命令。
            **kwargs: 命令边界接收的输入、输出和硬超时配置。

        Returns:
            镜像探测与清理成功时返回零状态。

        Raises:
            subprocess.TimeoutExpired: 真正的容器执行调用固定超时时抛出。
        """
        del kwargs
        calls.append(command)
        if command[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, _IMAGE_INSPECT_OUTPUT, "")
        if command[1] in {"kill", "rm", "version"} or command[1:3] == [
            "container",
            "ls",
        ]:
            return _confirmed_cleanup_result(command)
        raise subprocess.TimeoutExpired(command, 10, output="SECRET_HIDDEN_TEST")

    sandbox = DockerHumanEvalSandbox(
        command_runner=runner, token_factory=lambda: _CONTAINER_TOKEN
    )
    with pytest.raises(SandboxInfrastructureError, match="^timeout$"):
        sandbox.run(_problem(), "    return 1\n")

    assert calls[-4:] == [
        ["docker", "kill", _CONTAINER_NAME],
        ["docker", "rm", "-f", _CONTAINER_NAME],
        ["docker", "version", "--format", "{{.Server.Version}}"],
        _CLEANUP_QUERY,
    ]


def test_sandbox_nonzero_exit_cleans_and_confirms_named_container_absent() -> None:
    """Docker run 非零退出也必须完成并确认命名容器清理，再报告原始沙箱故障。"""
    calls: list[list[str]] = []

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """让固定镜像可信、容器执行失败，并模拟清理后容器确实不存在。

        Args:
            command: 镜像检查、容器执行或清理确认命令。
            **kwargs: 命令边界接收但本测试无需检查的参数。

        Returns:
            当前阶段对应的固定进程结果。
        """
        del kwargs
        calls.append(command)
        if command[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, _IMAGE_INSPECT_OUTPUT, "")
        if command[1] == "run":
            return subprocess.CompletedProcess(command, 1, "SECRET_HIDDEN_TEST", "")
        return _confirmed_cleanup_result(command)

    sandbox = DockerHumanEvalSandbox(
        command_runner=runner, token_factory=lambda: _CONTAINER_TOKEN
    )
    with pytest.raises(SandboxInfrastructureError, match="^sandbox_failed$"):
        sandbox.run(_problem(), "    return 1\n")

    assert calls[-4:] == [
        ["docker", "kill", _CONTAINER_NAME],
        ["docker", "rm", "-f", _CONTAINER_NAME],
        ["docker", "version", "--format", "{{.Server.Version}}"],
        _CLEANUP_QUERY,
    ]


def test_sandbox_escalates_when_abnormal_cleanup_cannot_be_confirmed() -> None:
    """异常容器路径若连 daemon 可达性都无法确认，必须升级为 cleanup_failed。"""

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """模拟容器非零、kill/rm 失败且随后 Docker daemon 不可达。

        Args:
            command: 镜像检查、容器执行或清理阶段命令。
            **kwargs: 命令边界接收但本测试无需检查的参数。

        Returns:
            镜像检查成功，其他命令均以非零状态失败。
        """
        del kwargs
        if command[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, _IMAGE_INSPECT_OUTPUT, "")
        return subprocess.CompletedProcess(command, 1, "SECRET_DAEMON_DETAIL", "")

    sandbox = DockerHumanEvalSandbox(
        command_runner=runner, token_factory=lambda: _CONTAINER_TOKEN
    )
    with pytest.raises(SandboxInfrastructureError, match="^cleanup_failed$") as raised:
        sandbox.run(_problem(), "    return 1\n")

    assert "SECRET_DAEMON_DETAIL" not in str(raised.value)


@pytest.mark.parametrize(
    ("query_returncode", "query_stdout", "query_stderr"),
    [(1, "", "SECRET_QUERY_DETAIL"), (0, f"{_IMAGE_ID}\n", ""), (0, "\n", "")],
)
def test_sandbox_cleanup_requires_successful_empty_exact_name_query(
    query_returncode: int, query_stdout: str, query_stderr: str
) -> None:
    """最终名字查询非零或返回残留 ID 时都必须升级为 cleanup_failed。

    Args:
        query_returncode: 模拟查询授权或瞬态错误的非零状态，或成功查询的零状态。
        query_stdout: 模拟仍存在容器的 ID、异常空白或严格空输出。
        query_stderr: 模拟不得解析或泄漏的 Docker CLI 动态错误详情。
    """

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """模拟异常运行后 daemon 可达，但最终容器查询不能证明为空。

        Args:
            command: 镜像检查、容器执行、清理或名字查询命令。
            **kwargs: 命令边界接收但本测试无需检查的参数。

        Returns:
            当前阶段的固定结果；只有名字查询使用参数化结果。

        Raises:
            AssertionError: 实现仍使用不能区分 not-found 与查询错误的旧命令。
        """
        del kwargs
        if command[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, _IMAGE_INSPECT_OUTPUT, "")
        if command[1] == "run":
            return subprocess.CompletedProcess(command, 1, "", "")
        # kill/rm 结果不能代替最终查询，因此允许二者竞态失败。
        if command[1] in {"kill", "rm"}:
            return subprocess.CompletedProcess(command, 1, "", "")
        if command[1] == "version":
            return subprocess.CompletedProcess(command, 0, "26.1", "")
        # 只有新的固定名字列表查询可以返回参数化的边界结果。
        if command == _CLEANUP_QUERY:
            return subprocess.CompletedProcess(
                command, query_returncode, query_stdout, query_stderr
            )
        raise AssertionError(f"unexpected cleanup command: {command}")

    sandbox = DockerHumanEvalSandbox(
        command_runner=runner, token_factory=lambda: _CONTAINER_TOKEN
    )
    with pytest.raises(SandboxInfrastructureError, match="^cleanup_failed$") as raised:
        sandbox.run(_problem(), "    return 1\n")

    assert "SECRET_QUERY_DETAIL" not in str(raised.value)


def test_sandbox_cleanup_still_removes_after_kill_subprocess_exception() -> None:
    """kill 命令自身抛出 subprocess 异常时仍必须继续 rm 并确认容器已经消失。"""
    calls: list[list[str]] = []

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """模拟 Docker run 非零与 kill 异常，其余清理确认步骤成功。

        Args:
            command: 镜像检查、容器执行或清理确认命令。
            **kwargs: 命令边界接收但本测试无需检查的参数。

        Returns:
            镜像检查、rm 和清理确认阶段的固定结果。

        Raises:
            subprocess.SubprocessError: kill 阶段模拟 Docker CLI 内部失败。
        """
        del kwargs
        calls.append(command)
        if command[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, _IMAGE_INSPECT_OUTPUT, "")
        if command[1] == "run":
            return subprocess.CompletedProcess(command, 1, "", "")
        if command[1] == "kill":
            raise subprocess.SubprocessError("SECRET_KILL_DETAIL")
        return _confirmed_cleanup_result(command)

    sandbox = DockerHumanEvalSandbox(
        command_runner=runner, token_factory=lambda: _CONTAINER_TOKEN
    )
    with pytest.raises(SandboxInfrastructureError, match="^sandbox_failed$"):
        sandbox.run(_problem(), "    return 1\n")

    assert ["docker", "rm", "-f", _CONTAINER_NAME] in calls


@pytest.mark.parametrize(
    "inspect_output",
    [
        f'{_IMAGE_ID}\t0:0\t["python","/opt/evalhub/verify.py"]\n',
        f'{_IMAGE_ID}\t10001:10001\t["python","-c","pass"]\n',
        'sha256:not-an-id\t10001:10001\t["python","/opt/evalhub/verify.py"]\n',
    ],
)
def test_sandbox_rejects_untrusted_local_image_before_sending_payload(
    inspect_output: str,
) -> None:
    """本地标签若不是固定用户、入口点和合法镜像 ID，隐藏载荷不得进入 docker run。

    Args:
        inspect_output: 模拟镜像检查返回的 ID、用户和入口点三元组。
    """
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """只允许执行一次镜像检查，记录是否意外收到候选标准输入。

        Args:
            command: 应为固定标签的只读镜像检查命令。
            **kwargs: 不应包含隐藏题目标准输入的检查参数。

        Returns:
            配置不可信但命令本身成功的镜像元数据。
        """
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, inspect_output, "")

    sandbox = DockerHumanEvalSandbox(command_runner=runner)
    with pytest.raises(SandboxInfrastructureError, match="^image_untrusted$"):
        sandbox.run(_problem(), "    return 1\n")

    assert len(calls) == 1
    assert "input" not in calls[0][1]
    assert "SECRET_HIDDEN_TEST" not in json.dumps(calls)


def test_sandbox_rejects_unexpected_verifier_fields() -> None:
    """即使容器返回通过，附带源码等额外字段也必须触发基础设施异常。"""

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """返回伪装成通过但携带隐藏内容的镜像响应。

        Args:
            command: 生产边界生成的固定 Docker 参数。
            **kwargs: 为兼容命令执行协议接收但不读取的参数。

        Returns:
            带未授权字段的零退出码进程结果。
        """
        del kwargs
        if command[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, _IMAGE_INSPECT_OUTPUT, "")
        if command[1] in {"kill", "rm", "version"} or command[1:3] == [
            "container",
            "ls",
        ]:
            return _confirmed_cleanup_result(command)
        return subprocess.CompletedProcess(
            command,
            0,
            '{"passed": true, "source": "SECRET_HIDDEN_TEST"}',
            "",
        )

    sandbox = DockerHumanEvalSandbox(command_runner=runner)
    with pytest.raises(SandboxInfrastructureError, match="^invalid_result$"):
        sandbox.run(_problem(), "    return 1\n")


def test_sandbox_rejects_non_string_failure_reason_as_infrastructure_error() -> None:
    """验证器原因即使是不可哈希对象，也必须安全转成脱敏基础设施异常。"""

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """返回带数组原因的畸形失败对象。

        Args:
            command: 生产边界生成的固定 Docker 参数。
            **kwargs: 为兼容命令执行协议接收但不读取的参数。

        Returns:
            零退出码但原因类型不合法的进程结果。
        """
        del kwargs
        if command[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, _IMAGE_INSPECT_OUTPUT, "")
        if command[1] in {"kill", "rm", "version"} or command[1:3] == [
            "container",
            "ls",
        ]:
            return _confirmed_cleanup_result(command)
        return subprocess.CompletedProcess(command, 0, '{"passed":false,"reason":[]}', "")

    sandbox = DockerHumanEvalSandbox(command_runner=runner)
    with pytest.raises(SandboxInfrastructureError, match="^invalid_result$"):
        sandbox.run(_problem(), "    return 1\n")


@pytest.mark.parametrize("reason", ["verification_failed", "timeout"])
def test_sandbox_scores_only_genuine_verifier_failures_as_zero(reason: str) -> None:
    """只有可信 controller 的断言失败或候选调用超时可成为正常零分。

    Args:
        reason: controller 明确定义为候选未通过的固定原因。
    """

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """返回可信镜像元数据和一个协议合法的候选失败对象。

        Args:
            command: 固定镜像探测或不可变 ID 容器执行命令。
            **kwargs: 命令边界接收但本测试无需检查的参数。

        Returns:
            对应阶段的零退出码进程结果。
        """
        del kwargs
        if command[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, _IMAGE_INSPECT_OUTPUT, "")
        if command[1] in {"kill", "rm", "version"} or command[1:3] == [
            "container",
            "ls",
        ]:
            return _confirmed_cleanup_result(command)
        output = json.dumps({"passed": False, "reason": reason})
        return subprocess.CompletedProcess(command, 0, output, "")

    result = DockerHumanEvalSandbox(command_runner=runner).run(_problem(), "    return 0\n")

    assert result == SandboxResult(passed=False, reason=reason)


@pytest.mark.parametrize("reason", ["invalid_payload", "execution_failed"])
def test_sandbox_treats_verifier_infrastructure_reasons_as_exceptions(reason: str) -> None:
    """controller 自身输入或执行故障不得伪装成候选零分。

    Args:
        reason: controller 固定报告的基础设施类失败原因。
    """

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """返回可信镜像元数据和一个协议合法但不可评分的故障对象。

        Args:
            command: 固定镜像探测或不可变 ID 容器执行命令。
            **kwargs: 命令边界接收但本测试无需检查的参数。

        Returns:
            对应阶段的零退出码进程结果。
        """
        del kwargs
        if command[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, _IMAGE_INSPECT_OUTPUT, "")
        if command[1] in {"kill", "rm", "version"} or command[1:3] == [
            "container",
            "ls",
        ]:
            return _confirmed_cleanup_result(command)
        output = json.dumps({"passed": False, "reason": reason})
        return subprocess.CompletedProcess(command, 0, output, "")

    sandbox = DockerHumanEvalSandbox(command_runner=runner)
    with pytest.raises(SandboxInfrastructureError, match=f"^{reason}$"):
        sandbox.run(_problem(), "    return 0\n")


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


def test_runner_aborts_when_sandbox_reports_infrastructure_error() -> None:
    """沙箱基础设施故障必须中止评测，不得生成零分样本或成功摘要。"""
    emitted: list[dict[str, object]] = []

    class FailingSandbox:
        """模拟在候选提交后发现执行环境不可用的沙箱。"""

        def run(self, problem: HumanEvalProblem, completion: str) -> SandboxResult:
            """无条件抛出类型化故障，证明 Runner 不会把它转换为判题失败。

            Args:
                problem: 当前待评测问题，本测试不读取其隐藏字段。
                completion: 已生成候选，本测试不执行它。

            Raises:
                SandboxInfrastructureError: 每次调用均报告固定镜像不可信。
            """
            del problem, completion
            raise SandboxInfrastructureError("image_untrusted")

    with pytest.raises(SandboxInfrastructureError, match="^image_untrusted$"):
        run_humaneval_benchmark(
            job_id="job_1",
            adapter=StaticMappingAdapter({"def one():\n": "    return 1\n"}),
            problems=[_problem()],
            sandbox=FailingSandbox(),
            on_sample_result=lambda sample, completed, total: emitted.append(sample),
        )

    assert emitted == []


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


def test_resume_summary_separates_evaluated_and_skipped_samples() -> None:
    """混合恢复运行必须明确是增量摘要，并仅对本轮新判题计算 Pass@1。"""
    first = _problem()
    second = replace(
        first,
        sample_id="hexagon_humaneval_02",
        prompt="def two():\n",
        entry_point="two",
        metadata={"source_key": "HumanEval/2", "input_zh": "实现返回 2 的函数。"},
    )
    sandbox = FakeSandbox(SandboxResult(passed=True))
    progress: list[tuple[int, int]] = []

    result = run_humaneval_benchmark(
        job_id="job_resume",
        adapter=StaticMappingAdapter({"def two():\n": "    return 2\n"}),
        problems=[first, second],
        sandbox=sandbox,
        skip_sample_ids=frozenset({first.sample_id}),
        on_progress=lambda completed, total: progress.append((completed, total)),
    )

    assert result["incremental"] is True
    assert result["total_samples"] == 2
    assert result["evaluated_samples"] == 1
    assert result["skipped_samples"] == 1
    assert result["passed_samples"] == 1
    assert result["average_score"] == 1.0
    assert progress == [(1, 2), (2, 2)]
    assert sandbox.calls == [("HumanEval/2", "    return 2\n")]


def test_resume_summary_uses_none_average_when_every_sample_is_skipped() -> None:
    """全量命中恢复缓存时不得伪造零分平均值，并应报告零条本轮评测。"""
    first = _problem()
    second = replace(
        first,
        sample_id="hexagon_humaneval_02",
        metadata={"source_key": "HumanEval/2", "input_zh": "实现返回 2 的函数。"},
    )
    sandbox = FakeSandbox(SandboxResult(passed=True))

    result = run_humaneval_benchmark(
        job_id="job_resume",
        adapter=StaticMappingAdapter({}),
        problems=[first, second],
        sandbox=sandbox,
        skip_sample_ids=frozenset({first.sample_id, second.sample_id}),
    )

    assert result["incremental"] is True
    assert result["total_samples"] == 2
    assert result["evaluated_samples"] == 0
    assert result["skipped_samples"] == 2
    assert result["passed_samples"] == 0
    assert result["average_score"] is None
    assert result["sample_results"] == []
    assert sandbox.calls == []


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
    expected_metadata = {
        "dataset": "hexagon-humaneval",
        "source_key": "HumanEval/1",
        "selection_stratum": "HumanEval/1",
        "evaluator_type": "pass@1",
        "entry_point": "one",
        "input_zh": "实现返回 1 的函数。",
        "reference_zh": None,
        "translation_version": "evalhub-zh-v1",
        "input_sha256": _digest("def one():\n"),
        "reference_sha256": _digest("    return 1\n"),
        "input_zh_sha256": _digest("实现返回 1 的函数。"),
        "reference_zh_sha256": None,
    }

    assert getattr(problems[0], "metadata", None) == expected_metadata

    assert problems == [
        HumanEvalProblem(
            sample_id="hexagon_humaneval_01",
            prompt="def one():\n",
            canonical_solution="    return 1\n",
            test="def check(candidate):\n    assert candidate() == 1\n",
            entry_point="one",
            metadata=expected_metadata,
        )
    ]
    emitted: list[dict[str, object]] = []
    result = run_humaneval_benchmark(
        job_id="job_provenance",
        adapter=StaticMappingAdapter({"def one():\n": "    return 2\n"}),
        problems=problems,
        sandbox=FakeSandbox(SandboxResult(passed=True)),
        on_sample_result=lambda sample, completed, total: emitted.append(sample),
    )

    assert emitted[0]["metadata"] == expected_metadata
    assert result["sample_results"][0]["metadata"] == expected_metadata
    serialized = json.dumps({"result": result, "emitted": emitted}, ensure_ascii=False)
    assert "def check(candidate)" not in serialized
    assert "    return 1\\n" not in serialized
    assert list(tmp_path.iterdir()) == [path]


def test_default_loader_rechecks_pinned_gzip_digest_immediately_before_parsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """生产缺省加载必须在读取 gzip 前复核固定摘要，拒绝准备后被替换的资产。

    Args:
        tmp_path: pytest 提供的隔离文件目录。
        monkeypatch: 用于把完整生产清单收窄为单条离线夹具的补丁工具。
    """
    import evalhub.datasets.hexagon_manifest as manifest_module
    import evalhub.datasets.hexagon_sources as sources_module

    selected = {
        "task_id": "HumanEval/1",
        "prompt": "def one():\n",
        "canonical_solution": "    return 1\n",
        "test": "def check(candidate):\n    assert candidate() == 1\n",
        "entry_point": "one",
    }
    path = tmp_path / "HumanEval.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        stream.write(json.dumps(selected) + "\n")
    expected_digest = hashlib.sha256(path.read_bytes()).hexdigest()

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
    source = replace(
        sources_module.hexagon_source_specs()["hexagon-humaneval"],
        sha256=expected_digest,
    )
    monkeypatch.setattr(manifest_module, "hexagon_manifest", lambda: (spec,))
    monkeypatch.setattr(
        sources_module,
        "hexagon_source_specs",
        lambda: {"hexagon-humaneval": source},
    )

    # 模拟 prepare 成功后、Runner 真正加载前被替换；内容故意不是 gzip 以证明先验摘要顺序。
    path.write_bytes(b"SECRET_CORRUPTED_ARCHIVE")
    with pytest.raises(ValueError, match="source SHA-256 mismatch") as raised:
        load_humaneval_problems(path)

    assert "SECRET_CORRUPTED_ARCHIVE" not in str(raised.value)


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
        output = "ok" if command[1] == "version" else _IMAGE_INSPECT_OUTPUT
        return subprocess.CompletedProcess(command, 0, output, "")

    readiness = benchmark_readiness(
        get_benchmark_spec("hexagon-humaneval"), command_runner=runner
    )

    assert readiness.ready is True
    assert readiness.code == "ready"
    assert commands[0][:2] == ["docker", "version"]
    assert commands[1][:3] == ["docker", "image", "inspect"]
    assert "--format" in commands[1]
    assert commands[1][-1] == "evalhub-humaneval:1.0.0"


def test_readiness_rejects_image_with_untrusted_runtime_config() -> None:
    """标签存在但用户或入口点不符时仍必须未就绪，避免隐藏载荷进入替代镜像。"""

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """让 Docker 服务成功，并返回使用 root 用户的镜像元数据。

        Args:
            command: Docker 服务版本或固定标签镜像检查命令。
            **kwargs: readiness 的只读捕获与短超时参数。

        Returns:
            服务探测或不可信镜像检查的零状态结果。
        """
        del kwargs
        if command[1] == "version":
            return subprocess.CompletedProcess(command, 0, "ok", "")
        output = f'{_IMAGE_ID}\t0:0\t["python","/opt/evalhub/verify.py"]\n'
        return subprocess.CompletedProcess(command, 0, output, "")

    readiness = benchmark_readiness(
        get_benchmark_spec("hexagon-humaneval"), command_runner=runner
    )

    assert readiness.ready is False
    assert readiness.code == "executor_not_ready"
    assert "./scripts/build_humaneval_image.sh" in readiness.message


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
