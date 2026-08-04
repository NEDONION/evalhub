"""在固定非特权容器中执行一条 HumanEval 候选并只输出安全 JSON 判定。"""

from __future__ import annotations

import json
import os
import signal
import sys
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from types import FrameType

_MAX_PAYLOAD_BYTES = 1024 * 1024
_FIELDS = {"prompt", "completion", "test", "entry_point"}
_alarm = signal.alarm
_set_signal = signal.signal


class VerificationTimeout(TimeoutError):
    """表示候选或隐藏校验超过容器内三秒硬时限。"""


def _raise_timeout(signum: int, frame: FrameType | None) -> None:
    """把 POSIX 闹钟转换为不包含候选上下文的固定超时异常。

    Args:
        signum: 触发回调的信号编号。
        frame: 信号到达时的 Python 栈帧；安全结果不会读取或序列化它。

    Raises:
        VerificationTimeout: 每次收到闹钟信号时无条件抛出。
    """
    del signum, frame
    raise VerificationTimeout


def _read_payload() -> dict[str, str]:
    """有界读取并严格校验 stdin 中唯一允许的四个字符串字段。

    Returns:
        已确认字段集合和类型完全匹配的执行载荷。

    Raises:
        ValueError: 输入超长、不是对象、字段不完整或入口点不是标识符时抛出。
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
    return payload


@contextmanager
def _suppress_untrusted_output() -> Iterator[None]:
    """在执行不可信源码期间同时丢弃 Python 流和原始 stdout/stderr 文件描述符。

    Yields:
        不可信执行完成或失败前保持生效的输出抑制上下文。

    Raises:
        OSError: 无法复制或恢复标准文件描述符时抛出，由顶层转成固定失败对象。
    """
    saved_stdout = os.dup(1)
    saved_stderr = os.dup(2)
    null_fd = os.open(os.devnull, os.O_WRONLY)
    try:
        # ``redirect_*`` 处理普通 print，``dup2`` 同时覆盖 os.write 与子进程继承路径。
        with open(os.devnull, "w", encoding="utf-8") as sink:
            with redirect_stdout(sink), redirect_stderr(sink):
                os.dup2(null_fd, 1)
                os.dup2(null_fd, 2)
                yield
    finally:
        # 先恢复标准描述符再关闭副本，保证最终 JSON 只由可信父逻辑写出。
        os.dup2(saved_stdout, 1)
        os.dup2(saved_stderr, 2)
        os.close(saved_stdout)
        os.close(saved_stderr)
        os.close(null_fd)


def _execute(payload: dict[str, str]) -> None:
    """组合官方提示、一次补全和隐藏测试，调用 ``check(candidate)`` 完成判定。

    Args:
        payload: 已通过严格四字段校验的 HumanEval 执行载荷。

    Raises:
        ValueError: 入口点或 ``check`` 不是可调用对象时抛出。
        BaseException: 候选或隐藏测试产生的任意失败由顶层统一脱敏处理。
    """
    namespace: dict[str, object] = {}
    source = f"{payload['prompt']}{payload['completion']}\n{payload['test']}"
    with _suppress_untrusted_output():
        exec(source, namespace)
        candidate = namespace.get(payload["entry_point"])
        checker = namespace.get("check")
        if not callable(candidate) or not callable(checker):
            raise ValueError("required callable is missing")
        checker(candidate)


def _write_result(passed: bool, reason: str | None = None) -> None:
    """向 stdout 写出唯一有界结果对象，不包含执行细节或动态异常文本。

    Args:
        passed: 隐藏测试是否完整通过。
        reason: 失败时使用的固定白名单原因；通过时必须为空。
    """
    payload: dict[str, object] = {"passed": passed}
    if reason is not None:
        payload["reason"] = reason
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def main() -> int:
    """读取一条载荷并在三秒闹钟内执行，所有路径都只返回固定 JSON。

    Returns:
        始终返回零；通过与失败只由 JSON ``passed`` 字段表达，防止宿主解析 stderr。
    """
    try:
        payload = _read_payload()
    except (ValueError, json.JSONDecodeError, UnicodeError):
        _write_result(False, "invalid_payload")
        return 0

    _set_signal(signal.SIGALRM, _raise_timeout)
    _alarm(3)
    try:
        _execute(payload)
    except VerificationTimeout:
        _write_result(False, "timeout")
    # 不可信代码可抛出 SystemExit 等任意异常；统一捕获是防止源码、环境和栈回显所必需。
    except BaseException:
        _write_result(False, "verification_failed")
    else:
        _write_result(True)
    finally:
        _alarm(0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
