"""执行单次候选函数调用，只通过受限 JSON 返回函数值。"""

from __future__ import annotations

import ctypes
import errno
import json
import os
import platform
import resource
import sys
from collections.abc import Callable

_MAX_PAYLOAD_BYTES = 1024 * 1024
_MAX_RESULT_BYTES = 64 * 1024
_FIELDS = {"prompt", "completion", "entry_point", "args", "kwargs"}
_PR_SET_NO_NEW_PRIVS = 38
_PR_SET_SECCOMP = 22
_SECCOMP_MODE_FILTER = 2
_SECCOMP_RET_KILL_PROCESS = 0x80000000
_SECCOMP_RET_ERRNO = 0x00050000
_SECCOMP_RET_ALLOW = 0x7FFF0000
_BPF_LOAD_WORD_ABSOLUTE = 0x20
_BPF_JUMP_EQUAL = 0x15
_BPF_JUMP_GREATER_OR_EQUAL = 0x35
_BPF_RETURN = 0x06
_SECCOMP_ARCH_OFFSET = 4
_SECCOMP_NUMBER_OFFSET = 0

Prctl = Callable[..., int]
ErrnoReader = Callable[[], int]
LimitSetter = Callable[[int, tuple[int, int]], None]
Instruction = tuple[int, int, int, int]

_SECCOMP_POLICIES: dict[str, tuple[int, dict[str, int]]] = {
    "x86_64": (
        0xC000003E,
        {
            "kill": 62,
            "tkill": 200,
            "tgkill": 234,
            "rt_sigqueueinfo": 129,
            "rt_tgsigqueueinfo": 297,
            "pidfd_send_signal": 424,
            "fork": 57,
            "vfork": 58,
            "clone": 56,
            "clone3": 435,
            "setsid": 112,
            "setpgid": 109,
        },
    ),
    "aarch64": (
        0xC00000B7,
        {
            "kill": 129,
            "tkill": 130,
            "tgkill": 131,
            "rt_sigqueueinfo": 138,
            "rt_tgsigqueueinfo": 240,
            "pidfd_send_signal": 424,
            # aarch64 的 libc fork/vfork 都落到通用 clone，三项必须共享同一拒绝规则。
            "fork": 220,
            "vfork": 220,
            "clone": 220,
            "clone3": 435,
            "setsid": 157,
            "setpgid": 154,
        },
    ),
}


class _SockFilter(ctypes.Structure):
    """镜像 Linux ``sock_filter`` 的经典 BPF 单条指令布局。"""

    _fields_ = [
        ("code", ctypes.c_ushort),
        ("jt", ctypes.c_ubyte),
        ("jf", ctypes.c_ubyte),
        ("k", ctypes.c_uint32),
    ]


class _SockFprog(ctypes.Structure):
    """镜像 Linux ``sock_fprog``，把有界 BPF 数组交给 ``prctl``。"""

    _fields_ = [
        ("length", ctypes.c_ushort),
        ("filters", ctypes.POINTER(_SockFilter)),
    ]


def _seccomp_policy(machine: str) -> tuple[int, dict[str, int]]:
    """返回受支持架构的 audit arch 和进程/信号逃逸 syscall 映射。

    Args:
        machine: ``platform.machine()`` 返回的 Linux 架构名称或常见等价名称。

    Returns:
        audit arch 常量和 syscall 名到编号的副本，调用方无法修改冻结策略。

    Raises:
        OSError: 架构不是固定镜像支持的 x86_64 或 aarch64 时失败关闭。
    """
    normalized = {"amd64": "x86_64", "arm64": "aarch64"}.get(machine, machine)
    try:
        arch, syscalls = _SECCOMP_POLICIES[normalized]
    except KeyError as exc:
        raise OSError(errno.ENOSYS, f"unsupported worker architecture: {machine}") from exc
    return arch, dict(syscalls)


def _seccomp_instructions(machine: str) -> tuple[Instruction, ...]:
    """构造先校验架构、再对逃逸 syscall 返回 EPERM 的经典 BPF 指令。

    Args:
        machine: 固定镜像当前 Linux 架构名称。

    Returns:
        可直接转换为 ``sock_filter`` 数组的不可变四元组序列。

    Raises:
        OSError: 架构不受支持时由 ``_seccomp_policy`` 失败关闭。
    """
    arch, syscalls = _seccomp_policy(machine)
    instructions: list[Instruction] = [
        (_BPF_LOAD_WORD_ABSOLUTE, 0, 0, _SECCOMP_ARCH_OFFSET),
        (_BPF_JUMP_EQUAL, 1, 0, arch),
        (_BPF_RETURN, 0, 0, _SECCOMP_RET_KILL_PROCESS),
        (_BPF_LOAD_WORD_ABSOLUTE, 0, 0, _SECCOMP_NUMBER_OFFSET),
    ]

    # x86_64 可用高位切换 x32 syscall 表；worker 不需要该 ABI，整体拒绝以免编号绕过。
    if arch == 0xC000003E:
        instructions.append((_BPF_JUMP_GREATER_OR_EQUAL, 0, 1, 0x40000000))
        instructions.append((_BPF_RETURN, 0, 0, _SECCOMP_RET_ERRNO | errno.EPERM))

    # 每个匹配项立即返回 EPERM；不匹配则越过返回指令继续检查下一项。
    for syscall_number in sorted(set(syscalls.values())):
        instructions.append((_BPF_JUMP_EQUAL, 0, 1, syscall_number))
        instructions.append((_BPF_RETURN, 0, 0, _SECCOMP_RET_ERRNO | errno.EPERM))
    instructions.append((_BPF_RETURN, 0, 0, _SECCOMP_RET_ALLOW))
    return tuple(instructions)


def _install_seccomp_policy(
    *,
    machine: str | None = None,
    prctl: Prctl | None = None,
    errno_reader: ErrnoReader = ctypes.get_errno,
) -> None:
    """以不可逆 no-new-privileges 安装 worker 本地 seccomp-BPF 逃逸策略。

    Args:
        machine: 测试可注入的架构；生产缺省读取当前 Linux 机器类型。
        prctl: 测试可注入的 Linux ``prctl``；生产从当前 C 运行库读取。
        errno_reader: 读取最近 C 调用 errno 的可替换边界。

    Raises:
        OSError: 非 Linux/未知架构，或内核拒绝任一步安全策略时失败关闭。
    """
    if machine is None:
        if sys.platform != "linux":
            raise OSError(errno.ENOSYS, "worker seccomp requires Linux")
        machine = platform.machine()
    instructions = _seccomp_instructions(machine)

    # ctypes 数组和 program 必须存活到 prctl 返回，随后过滤器已由内核复制。
    filter_array_type = _SockFilter * len(instructions)
    filter_array = filter_array_type(*(_SockFilter(*item) for item in instructions))
    program = _SockFprog(len(instructions), filter_array)
    if prctl is None:
        libc = ctypes.CDLL(None, use_errno=True)
        prctl = libc.prctl
    if prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise OSError(errno_reader(), "cannot set worker no-new-privileges")
    if prctl(_PR_SET_SECCOMP, _SECCOMP_MODE_FILTER, ctypes.byref(program), 0, 0) != 0:
        raise OSError(errno_reader(), "cannot install worker seccomp policy")


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


def _limit_worker_resources(*, limit_setter: LimitSetter = resource.setrlimit) -> None:
    """降低 worker 文件大小和用户进程数硬限制，作为 seccomp 的纵深防御。

    Args:
        limit_setter: 测试可注入的 ``resource.setrlimit`` 兼容调用。

    Raises:
        OSError: 内核拒绝设置硬限制时抛出，worker 随后以失败状态退出。
        ValueError: 平台资源限制参数无效时抛出。
    """
    limit_setter(resource.RLIMIT_FSIZE, (_MAX_RESULT_BYTES, _MAX_RESULT_BYTES))
    limit_setter(resource.RLIMIT_NPROC, (1, 1))


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
        _limit_worker_resources()
        payload = _read_payload()
        _install_seccomp_policy()
        result = _encode_result(_execute(payload))
        os.write(sys.stdout.fileno(), result)
    except BaseException:
        # worker 不输出 traceback 或异常文本，最终通过状态也从不由该进程决定。
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
