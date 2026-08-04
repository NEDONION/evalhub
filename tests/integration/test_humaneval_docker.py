"""通过显式启用的真实 Docker 镜像验证 HumanEval 正确与错误候选。"""

import os

import pytest

from evalhub.benchmarks.humaneval import DockerHumanEvalSandbox, HumanEvalProblem

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("EVALHUB_RUN_DOCKER_TESTS") != "1",
        reason="set EVALHUB_RUN_DOCKER_TESTS=1 to run Docker integration",
    ),
]


def test_fixed_docker_image_accepts_canonical_and_rejects_incorrect_completion() -> None:
    """固定镜像应让标准实现通过、错误实现失败且只返回安全短原因。"""
    problem = HumanEvalProblem(
        sample_id="integration_humaneval",
        source_key="HumanEval/integration",
        prompt="def add(a, b):\n",
        canonical_solution="    return a + b\n",
        test="def check(candidate):\n    assert candidate(2, 3) == 5\n",
        entry_point="add",
        input_zh="实现两个数字相加。",
    )
    sandbox = DockerHumanEvalSandbox()

    # 两次调用都经过真实 Docker 边界，测试进程从不导入或执行候选源码。
    passed = sandbox.run(problem, problem.canonical_solution)
    failed = sandbox.run(problem, "    return a - b\n")

    assert passed.passed is True
    assert passed.reason is None
    assert failed.passed is False
    assert failed.reason == "verification_failed"


def test_hostile_candidate_cannot_reach_controller_frames_or_globals() -> None:
    """候选枚举父帧与全局表时看不到可信 controller，正确函数值仍由 check 判定。"""
    problem = HumanEvalProblem(
        sample_id="integration_hostile_humaneval",
        source_key="HumanEval/integration-hostile",
        prompt="def add(a, b):\n",
        canonical_solution="    return a + b\n",
        test="def check(candidate):\n    assert candidate(2, 3) == 5\n",
        entry_point="add",
        input_zh="实现两个数字相加。",
    )
    completion = (
        "    import inspect, signal\n"
        "    signal.alarm(0)\n"
        "    frame = inspect.currentframe()\n"
        "    while frame is not None:\n"
        "        frame.f_globals.get('_write_result', lambda *args: None)(True)\n"
        "        frame = frame.f_back\n"
        "    return a + b\n"
    )

    result = DockerHumanEvalSandbox().run(problem, completion)

    assert result.passed is True
    assert result.reason is None


def test_candidate_file_descriptor_writes_cannot_pollute_host_verdict() -> None:
    """候选写遍常见文件描述符时只会破坏自己的 RPC，宿主仍收到固定失败原因。"""
    problem = HumanEvalProblem(
        sample_id="integration_fd_humaneval",
        source_key="HumanEval/integration-fd",
        prompt="def add(a, b):\n",
        canonical_solution="    return a + b\n",
        test="def check(candidate):\n    assert candidate(2, 3) == 5\n",
        entry_point="add",
        input_zh="实现两个数字相加。",
    )
    completion = (
        "    import os\n"
        "    for fd in range(1, 64):\n"
        "        try:\n"
        "            os.write(fd, b'SECRET_HIDDEN_TEST\\n')\n"
        "        except OSError:\n"
        "            pass\n"
        "    return a + b\n"
    )

    result = DockerHumanEvalSandbox().run(problem, completion)

    assert result.passed is False
    assert result.reason == "verification_failed"


def test_candidate_cannot_forge_pass_by_exiting_with_old_success_code() -> None:
    """候选主动使用旧版成功退出码时必须失败，最终 verdict 只能来自隐藏 check。"""
    problem = HumanEvalProblem(
        sample_id="integration_exit_forgery_humaneval",
        source_key="HumanEval/integration-exit-forgery",
        prompt="def add(a, b):\n",
        canonical_solution="    return a + b\n",
        test="def check(candidate):\n    assert candidate(2, 3) == 5\n",
        entry_point="add",
        input_zh="实现两个数字相加。",
    )

    result = DockerHumanEvalSandbox().run(problem, "    import os\n    os._exit(73)\n")

    assert result.passed is False
    assert result.reason == "verification_failed"


@pytest.mark.parametrize("signal_name", ["SIGKILL", "SIGSTOP"])
def test_candidate_cannot_signal_trusted_controller(signal_name: str) -> None:
    """候选向父 controller 发送不可捕获信号时必须得到 EPERM，容器仍返回可信 verdict。

    Args:
        signal_name: 不能由 Python handler 捕获或忽略的终止、暂停信号名称。
    """
    problem = HumanEvalProblem(
        sample_id=f"integration_signal_{signal_name.lower()}_humaneval",
        source_key=f"HumanEval/integration-signal-{signal_name.lower()}",
        prompt="def add(a, b):\n",
        canonical_solution="    return a + b\n",
        test="def check(candidate):\n    assert candidate(2, 3) == 5\n",
        entry_point="add",
        input_zh="实现两个数字相加。",
    )
    completion = (
        "    import os, signal\n"
        "    try:\n"
        f"        os.kill(os.getppid(), signal.{signal_name})\n"
        "    except PermissionError:\n"
        "        return a + b\n"
        "    return -1\n"
    )

    result = DockerHumanEvalSandbox().run(problem, completion)

    assert result.passed is True
    assert result.reason is None


def test_candidate_cannot_fork_and_create_an_escaping_session() -> None:
    """候选 fork/setsid 逃逸必须被 worker 本地策略拒绝，隐藏测试仍能完成判定。"""
    problem = HumanEvalProblem(
        sample_id="integration_process_escape_humaneval",
        source_key="HumanEval/integration-process-escape",
        prompt="def add(a, b):\n",
        canonical_solution="    return a + b\n",
        test="def check(candidate):\n    assert candidate(2, 3) == 5\n",
        entry_point="add",
        input_zh="实现两个数字相加。",
    )
    completion = (
        "    import os\n"
        "    try:\n"
        "        child = os.fork()\n"
        "    except OSError:\n"
        "        try:\n"
        "            os.setsid()\n"
        "        except PermissionError:\n"
        "            return a + b\n"
        "        return -1\n"
        "    if child == 0:\n"
        "        try:\n"
        "            os.setsid()\n"
        "        finally:\n"
        "            os._exit(0)\n"
        "    os.waitpid(child, 0)\n"
        "    return -1\n"
    )

    result = DockerHumanEvalSandbox().run(problem, completion)

    assert result.passed is True
    assert result.reason is None


def test_candidate_cannot_signal_controller_through_async_io_ownership() -> None:
    """候选不得用 socket/pipe 的异步 I/O 所有者通知向 controller 发送 SIGIO。"""
    problem = HumanEvalProblem(
        sample_id="integration_async_io_signal_humaneval",
        source_key="HumanEval/integration-async-io-signal",
        prompt="def add(a, b):\n",
        canonical_solution="    return a + b\n",
        test="def check(candidate):\n    assert candidate(2, 3) == 5\n",
        entry_point="add",
        input_zh="实现两个数字相加。",
    )
    completion = (
        "    import fcntl, os, signal, socket\n"
        "    blocked = 0\n"
        "    try:\n"
        "        reader, writer = socket.socketpair()\n"
        "        fcntl.fcntl(reader, fcntl.F_SETOWN, os.getppid())\n"
        "        fcntl.fcntl(reader, fcntl.F_SETSIG, signal.SIGIO)\n"
        "        flags = fcntl.fcntl(reader, fcntl.F_GETFL)\n"
        "        fcntl.fcntl(reader, fcntl.F_SETFL, flags | os.O_ASYNC)\n"
        "        writer.send(b'x')\n"
        "    except PermissionError:\n"
        "        blocked += 1\n"
        "    read_fd, write_fd = os.pipe()\n"
        "    try:\n"
        "        fcntl.fcntl(read_fd, fcntl.F_SETOWN, os.getppid())\n"
        "    except PermissionError:\n"
        "        blocked += 1\n"
        "    return a + b if blocked == 2 else -1\n"
    )

    result = DockerHumanEvalSandbox().run(problem, completion)

    assert result.passed is True
    assert result.reason is None


def test_candidate_cannot_lower_controller_hard_limits_with_prlimit() -> None:
    """候选用 prlimit64 指向父 controller 时必须得到 EPERM，隐藏检查仍可完成。"""
    problem = HumanEvalProblem(
        sample_id="integration_parent_prlimit_humaneval",
        source_key="HumanEval/integration-parent-prlimit",
        prompt="def add(a, b):\n",
        canonical_solution="    return a + b\n",
        test="def check(candidate):\n    assert candidate(2, 3) == 5\n",
        entry_point="add",
        input_zh="实现两个数字相加。",
    )
    completion = (
        "    import os, resource\n"
        "    try:\n"
        "        resource.prlimit(os.getppid(), resource.RLIMIT_NOFILE, (0, 0))\n"
        "    except PermissionError:\n"
        "        return a + b\n"
        "    return -1\n"
    )

    result = DockerHumanEvalSandbox().run(problem, completion)

    assert result.passed is True
    assert result.reason is None
