"""执行单次候选函数调用，只通过受限 JSON 返回函数值。"""

from __future__ import annotations

import json
import os
import resource
import sys

_MAX_PAYLOAD_BYTES = 1024 * 1024
_MAX_RESULT_BYTES = 64 * 1024
_FIELDS = {"prompt", "completion", "entry_point", "args", "kwargs"}


def _read_payload() -> dict[str, object]:
    """有界读取只含候选源码与本次调用参数的 JSON RPC 载荷。

    Returns:
        字段完整、基础类型正确且不包含隐藏测试的调用对象。

    Raises:
        ValueError: 输入超长、字段或基础类型不符合固定协议时抛出。
        json.JSONDecodeError: 输入不是合法 JSON 时抛出。
    """
    raw = sys.stdin.buffer.read(_MAX_PAYLOAD_BYTES + 1)
    if len(raw) > _MAX_PAYLOAD_BYTES:
        raise ValueError("payload is too large")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or set(payload) != _FIELDS:
        raise ValueError("payload fields are invalid")

    # worker 只接受候选执行必需字段，协议中不存在隐藏 test 或最终 verdict。
    text_fields = ("prompt", "completion", "entry_point")
    if any(not isinstance(payload[field], str) for field in text_fields):
        raise ValueError("candidate fields must be strings")
    if not payload["prompt"] or not str(payload["entry_point"]).isidentifier():
        raise ValueError("candidate fields are invalid")
    if not isinstance(payload["args"], list) or not isinstance(payload["kwargs"], dict):
        raise ValueError("candidate arguments are invalid")
    if any(not isinstance(key, str) for key in payload["kwargs"]):
        raise ValueError("candidate keyword names are invalid")
    return payload


def _limit_output_file() -> None:
    """限制 worker 及其子进程可写单文件大小，约束恶意 stdout 和临时文件。

    Raises:
        OSError: 内核拒绝设置硬限制时抛出，worker 随后以失败状态退出。
        ValueError: 平台资源限制参数无效时抛出。
    """
    resource.setrlimit(resource.RLIMIT_FSIZE, (_MAX_RESULT_BYTES, _MAX_RESULT_BYTES))


def _execute(payload: dict[str, object]) -> object:
    """在一次性进程命名空间中加载候选并执行一个函数调用。

    Args:
        payload: 已通过固定字段校验的候选源码与 JSON 参数。

    Returns:
        候选函数返回的原始对象，随后必须由 JSON 编码进一步收窄。

    Raises:
        ValueError: 指定入口点不是可调用对象时抛出。
        BaseException: 候选执行产生的任意失败由 ``main`` 转成非零退出。
    """
    namespace: dict[str, object] = {}
    source = f"{payload['prompt']}{payload['completion']}"
    exec(source, namespace)
    candidate = namespace.get(str(payload["entry_point"]))
    if not callable(candidate):
        raise ValueError("candidate callable is missing")
    args = payload["args"]
    kwargs = payload["kwargs"]
    if not isinstance(args, list) or not isinstance(kwargs, dict):
        raise ValueError("candidate arguments are invalid")
    return candidate(*args, **kwargs)


def _encode_result(value: object) -> bytes:
    """把候选返回值编码为唯一成功对象，并拒绝过大的动态输出。

    Args:
        value: 候选函数返回的对象。

    Returns:
        不超过 64 KiB 的紧凑 UTF-8 JSON 成功对象。

    Raises:
        TypeError: 返回值不是 JSON 原生类型时抛出。
        ValueError: 返回值包含非有限数字或编码后超过上限时抛出。
    """
    result = json.dumps(
        {"ok": True, "value": value},
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(result) > _MAX_RESULT_BYTES:
        raise ValueError("candidate result is too large")
    return result


def main() -> int:
    """执行一次候选 RPC；只有包装器成功写出函数值时才返回零。

    Returns:
        零表示输出通道包含待由 controller 校验的候选值，其他值表示调用失败。
    """
    try:
        _limit_output_file()
        payload = _read_payload()
        result = _encode_result(_execute(payload))
        os.write(sys.stdout.fileno(), result)
    except BaseException:
        # worker 不输出 traceback 或异常文本，最终通过状态也从不由该进程决定。
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
