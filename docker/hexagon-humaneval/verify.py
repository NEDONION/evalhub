"""在可信 controller 中执行隐藏测试，并把候选调用隔离为受限 RPC。"""

from __future__ import annotations

import ctypes
import json
import math
import os
import signal
import subprocess
import sys
import tempfile
from collections.abc import Callable

_MAX_PAYLOAD_BYTES = 1024 * 1024
_MAX_WORKER_RESULT_BYTES = 64 * 1024
_MAX_JSON_DEPTH = 16
_FIELDS = {"prompt", "completion", "test", "entry_point"}
_WORKER_PATH = "/opt/evalhub/worker.py"

ProcessFactory = Callable[..., subprocess.Popen[bytes]]
GroupKiller = Callable[[int], None]
Prctl = Callable[[int, int, int, int, int], int]
CandidateRunner = Callable[[dict[str, str], tuple[object, ...], dict[str, object]], object]


class CandidateCallError(RuntimeError):
    """表示候选 worker 没有返回一个合法、受限的函数值。"""


class CandidateTimeout(CandidateCallError):
    """表示单次候选函数调用超过隔离 worker 的硬时限。"""


class ControllerInfrastructureError(RuntimeError):
    """表示可信 controller 无法创建、等待或读取隔离 worker。"""


def _read_payload() -> dict[str, str]:
    """有界读取并严格校验 stdin 中唯一允许的四个字符串字段。

    Returns:
        四个字段均为字符串且入口点合法的评测载荷。

    Raises:
        ValueError: 输入超长、字段不完整、类型错误或入口点无效时抛出。
        json.JSONDecodeError: 输入不是合法 JSON 时抛出。
    """
    raw = sys.stdin.buffer.read(_MAX_PAYLOAD_BYTES + 1)
    if len(raw) > _MAX_PAYLOAD_BYTES:
        raise ValueError("payload is too large")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or set(payload) != _FIELDS:
        raise ValueError("payload fields are invalid")
    if any(not isinstance(payload[field], str) for field in _FIELDS):
        raise ValueError("payload fields must be strings")

    # 提示、测试和入口点必须非空；候选允许为空并由隐藏测试正常判为失败。
    if not payload["prompt"] or not payload["test"] or not payload["entry_point"]:
        raise ValueError("required payload field is empty")
    if not payload["entry_point"].isidentifier():
        raise ValueError("entry point is invalid")
    return {field: payload[field] for field in _FIELDS}


def _lock_controller_process(*, prctl: Prctl | None = None) -> None:
    """禁止同 UID worker 通过 ptrace 或 ``/proc/<pid>/fd`` 访问 controller。

    Args:
        prctl: 测试可注入的 Linux ``prctl`` 调用；缺省从当前 C 运行库读取。

    Raises:
        OSError: 内核拒绝 ``PR_SET_DUMPABLE=0`` 时抛出，controller 随后拒绝执行候选。
    """
    if prctl is None:
        libc = ctypes.CDLL(None, use_errno=True)
        prctl = libc.prctl
    if prctl(4, 0, 0, 0, 0) != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, "cannot protect verifier controller")


def _kill_worker_group(process_id: int) -> None:
    """终止 worker 会话中的候选进程树，避免后台子进程越过单次调用边界。

    Args:
        process_id: 同时作为独立会话进程组 ID 的 worker PID。
    """
    try:
        os.killpg(process_id, signal.SIGKILL)
    except ProcessLookupError:
        # worker 没有派生后台进程时，主进程退出后进程组可能已经自然消失。
        return


def _cleanup_worker_group(process_id: int, group_killer: GroupKiller) -> None:
    """执行可注入的进程组清理，并把内核拒绝收敛为 controller 基础设施故障。

    Args:
        process_id: 待回收 worker 的独立会话进程组 ID。
        group_killer: 生产使用 ``killpg``、测试可替换的清理边界。

    Raises:
        ControllerInfrastructureError: 操作系统无法完成进程组回收时抛出。
    """
    try:
        group_killer(process_id)
    except OSError as exc:
        raise ControllerInfrastructureError("cannot clean up candidate worker") from exc


def _validate_json_value(value: object, *, depth: int = 0) -> None:
    """确认跨隔离边界的值只包含有限深度的 JSON 原生类型。

    Args:
        value: 隐藏测试参数或 worker 返回值。
        depth: 当前递归深度，仅供内部递归累计。

    Raises:
        ValueError: 值包含自定义对象、非字符串键、非有限浮点数或嵌套过深时抛出。
    """
    if depth > _MAX_JSON_DEPTH:
        raise ValueError("JSON value is nested too deeply")
    if value is None or type(value) in {bool, int, str}:
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("JSON float must be finite")
        return

    # 只接受真正的 list 和 dict，避免调用不可信容器子类的迭代或取值方法。
    if type(value) is list:
        for item in value:
            _validate_json_value(item, depth=depth + 1)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _validate_json_value(item, depth=depth + 1)
        return
    raise ValueError("value is not JSON-safe")


def _candidate_payload(
    payload: dict[str, str], args: tuple[object, ...], kwargs: dict[str, object]
) -> bytes:
    """序列化单次候选调用，并明确排除隐藏测试源码。

    Args:
        payload: controller 已校验的原始四字段载荷。
        args: 隐藏 ``check`` 传给候选的位置参数。
        kwargs: 隐藏 ``check`` 传给候选的关键字参数。

    Returns:
        不超过一 MiB、仅含候选源码和 JSON 安全参数的紧凑 JSON 字节。

    Raises:
        CandidateCallError: 参数不适合安全 JSON RPC 或序列化后超过上限时抛出。
    """
    try:
        _validate_json_value(list(args))
        _validate_json_value(kwargs)
        message = json.dumps(
            {
                "prompt": payload["prompt"],
                "completion": payload["completion"],
                "entry_point": payload["entry_point"],
                "args": list(args),
                "kwargs": kwargs,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (KeyError, TypeError, ValueError, RecursionError) as exc:
        raise CandidateCallError("candidate arguments are not JSON-safe") from exc
    if len(message) > _MAX_PAYLOAD_BYTES:
        raise CandidateCallError("candidate call is too large")
    return message


def _parse_worker_result(raw: bytes) -> object:
    """解析 worker 唯一允许返回的候选函数值，不接受 verdict 或动态诊断。

    Args:
        raw: 从容器内临时文件有界读取的完整 worker 输出。

    Returns:
        经类型和深度校验的 JSON 原生候选返回值。

    Raises:
        CandidateCallError: 输出为空、过长、畸形、带额外字段或值不安全时抛出。
    """
    if not raw or len(raw) > _MAX_WORKER_RESULT_BYTES:
        raise CandidateCallError("candidate result size is invalid")
    try:
        message = json.loads(raw)
        if not isinstance(message, dict) or set(message) != {"ok", "value"}:
            raise ValueError("candidate result fields are invalid")
        if message["ok"] is not True:
            raise ValueError("candidate result status is invalid")
        _validate_json_value(message["value"])
    except (json.JSONDecodeError, UnicodeError, ValueError, RecursionError) as exc:
        raise CandidateCallError("candidate result is invalid") from exc
    return message["value"]


def _run_candidate(
    payload: dict[str, str],
    args: tuple[object, ...],
    kwargs: dict[str, object],
    *,
    process_factory: ProcessFactory = subprocess.Popen,
    group_killer: GroupKiller = _kill_worker_group,
) -> object:
    """在独立 worker 中执行一次候选调用，并只读取有界 JSON 函数值。

    Args:
        payload: controller 已校验的原始四字段载荷。
        args: 隐藏测试传给候选的位置参数。
        kwargs: 隐藏测试传给候选的关键字参数。
        process_factory: 创建 worker 子进程的可替换边界。
        group_killer: 回收 worker 及其派生进程组的可替换边界。

    Returns:
        worker 返回并通过窄 JSON 协议校验的候选函数值。

    Raises:
        CandidateCallError: worker 失败或没有返回合法函数值时抛出。
        CandidateTimeout: worker 超过单次调用硬时限时抛出。
        ControllerInfrastructureError: controller 无法管理本地进程或临时通道时抛出。
    """
    call_payload = _candidate_payload(payload, args, kwargs)
    try:
        output_file = tempfile.TemporaryFile()
    except OSError as exc:
        raise ControllerInfrastructureError("cannot create worker result channel") from exc

    # worker 的 stdout 只写入容器内限额临时文件；宿主永远不会捕获这条动态通道。
    with output_file:
        try:
            process = process_factory(
                [sys.executable, _WORKER_PATH],
                stdin=subprocess.PIPE,
                stdout=output_file,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
            )
        except OSError as exc:
            raise ControllerInfrastructureError("cannot start candidate worker") from exc
        try:
            process.communicate(input=call_payload, timeout=3)
        except subprocess.TimeoutExpired as exc:
            _cleanup_worker_group(process.pid, group_killer)
            try:
                process.communicate()
            except OSError as wait_error:
                raise ControllerInfrastructureError("cannot reap candidate worker") from wait_error
            raise CandidateTimeout("candidate call timed out") from exc
        except OSError as exc:
            _cleanup_worker_group(process.pid, group_killer)
            raise ControllerInfrastructureError("cannot wait for candidate worker") from exc
        else:
            # 正常退出也清除候选可能留下的同会话后台进程。
            _cleanup_worker_group(process.pid, group_killer)

        if process.returncode != 0:
            raise CandidateCallError("candidate worker failed")
        try:
            output_file.seek(0)
            result = output_file.read(_MAX_WORKER_RESULT_BYTES + 1)
        except OSError as exc:
            raise ControllerInfrastructureError("cannot read candidate result") from exc
    return _parse_worker_result(result)


def _verify(
    payload: dict[str, str], *, candidate_runner: CandidateRunner = _run_candidate
) -> dict[str, object]:
    """只在可信 controller 中执行隐藏 ``check``，候选仅以 RPC 代理出现。

    Args:
        payload: 已严格校验的提示、候选、隐藏测试和入口点。
        candidate_runner: 测试可注入的隔离候选调用边界。

    Returns:
        只含 ``passed`` 及可选固定 ``reason`` 的最终判定。
    """
    namespace: dict[str, object] = {}
    try:
        exec(payload["test"], namespace)
        checker = namespace.get("check")
        if not callable(checker):
            raise ValueError("hidden check is missing")

        # 代理只转交本次调用参数；不可信候选源码从未进入 controller 的执行栈或全局表。
        def candidate(*args: object, **kwargs: object) -> object:
            """把隐藏测试的一次函数调用转成隔离 worker RPC。

            Args:
                *args: 隐藏测试提供的 JSON 安全位置参数。
                **kwargs: 隐藏测试提供的 JSON 安全关键字参数。

            Returns:
                隔离 worker 通过窄 JSON 协议返回的候选函数值。
            """
            return candidate_runner(payload, args, kwargs)

        checker(candidate)
    except CandidateTimeout:
        return {"passed": False, "reason": "timeout"}
    except ControllerInfrastructureError:
        return {"passed": False, "reason": "execution_failed"}
    except BaseException:
        # 候选异常、伪造输出和隐藏断言失败都只构成真实未通过，不回传动态详情。
        return {"passed": False, "reason": "verification_failed"}
    return {"passed": True}


def _write_result(payload: dict[str, object]) -> None:
    """由可信 controller 向宿主通道写出唯一固定结果对象。

    Args:
        payload: `_verify` 或输入验证生成的白名单结果。
    """
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def main() -> int:
    """验证输入、保护 controller 并执行隐藏测试，故障只输出固定 JSON。

    Returns:
        始终返回零；通过、候选失败和基础设施失败只由固定 JSON 表达。
    """
    try:
        payload = _read_payload()
        _lock_controller_process()
        result = _verify(payload)
    except (ValueError, json.JSONDecodeError, UnicodeError):
        result = {"passed": False, "reason": "invalid_payload"}
    except (OSError, subprocess.SubprocessError):
        result = {"passed": False, "reason": "execution_failed"}
    _write_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
