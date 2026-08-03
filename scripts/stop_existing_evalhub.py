#!/usr/bin/env python3
"""安全识别并优雅停止占用目标端口的旧 EvalHub 本地服务。"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from urllib.request import urlopen


def normalize_probe_host(host: str) -> str:
    """把无法直接作为客户端目标的通配监听地址转换为本机探测地址。

    Args:
        host: EvalHub 启动时使用的监听主机地址。

    Returns:
        可供健康检查请求访问的主机地址。
    """
    # ``0.0.0.0`` 只表示监听全部网卡，客户端必须改用具体回环地址发起请求。
    return "127.0.0.1" if host == "0.0.0.0" else host


def listener_pids(port: int) -> list[int]:
    """使用 ``lsof`` 查询正在监听指定 TCP 端口的全部进程编号。

    Args:
        port: 需要检查的本地 TCP 端口。

    Returns:
        去重并升序排列的监听进程编号列表，空端口返回空列表。

    Raises:
        RuntimeError: 系统缺少 ``lsof``、命令异常退出或输出了无效进程编号。
    """
    # 使用参数列表执行命令，避免端口值经过 shell 插值，并只筛选处于监听状态的进程。
    try:
        result = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        # 把平台工具缺失转换为面向启动流程的明确依赖错误，同时保留原始异常链。
        raise RuntimeError("lsof is required to inspect the EvalHub port") from exc

    # ``lsof`` 以 1 表示没有匹配结果；其他非零状态才代表真实的查询失败。
    if result.returncode not in (0, 1):
        message = result.stderr.strip() or f"lsof failed with exit code {result.returncode}"
        raise RuntimeError(message)

    # 对进程编号去重并排序，确保终止顺序和测试结果不受命令输出顺序影响。
    try:
        return sorted({int(line) for line in result.stdout.splitlines() if line.strip()})
    except ValueError as exc:
        # 非数字输出说明系统工具响应不符合协议，不能把不完整结果当作安全依据。
        raise RuntimeError(f"lsof returned an invalid PID for port {port}") from exc


def is_evalhub(host: str, port: int) -> bool:
    """通过健康检查确认端口监听者是否确实为 EvalHub 服务。

    Args:
        host: 服务监听主机地址，允许传入 ``0.0.0.0``。
        port: 待确认服务使用的本地端口。

    Returns:
        健康接口返回成功且服务标识为 ``evalhub`` 时返回 ``True``。
    """
    # 先规范化监听地址，再访问固定健康端点，避免仅凭进程名误杀其他服务。
    probe_host = normalize_probe_host(host)
    url = f"http://{probe_host}:{port}/api/health"
    try:
        # 一秒超时限制启动前探测耗时，并将响应正文按 JSON 协议解析。
        with urlopen(url, timeout=1.0) as response:
            payload = json.load(response)
            # 状态码、对象类型和服务标识必须同时满足，才允许后续发送终止信号。
            return (
                response.status == 200
                and isinstance(payload, dict)
                and payload.get("service") == "evalhub"
            )
    except (OSError, ValueError):
        # 连接失败或 JSON 无效均视为身份无法确认，由调用方拒绝终止端口监听者。
        return False


def stop_existing_evalhub(host: str, port: int, timeout: float = 5.0) -> list[int]:
    """仅在确认服务身份后优雅停止目标端口上的旧 EvalHub。

    Args:
        host: EvalHub 服务的监听主机地址。
        port: 需要释放的本地 TCP 端口。
        timeout: 发送 ``SIGTERM`` 后等待端口释放的最长秒数。

    Returns:
        已发送终止信号的旧 EvalHub 进程编号；端口空闲时返回空列表。

    Raises:
        RuntimeError: 端口属于其他服务、无权终止进程或超时后端口仍未释放。
    """
    # 先查询真实监听者；端口本来空闲时无需执行健康请求或进程操作。
    pids = listener_pids(port)
    if not pids:
        return []

    # 身份校验是进程终止前的安全门，未知服务即使占用目标端口也绝不处理。
    if not is_evalhub(host, port):
        joined = ", ".join(str(pid) for pid in pids)
        raise RuntimeError(f"port {port} is occupied by PID {joined}, but it is not EvalHub")

    # 只发送可被应用正常处理的 ``SIGTERM``，不自动升级为不可恢复的强制终止。
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            # 查询后自然退出的进程已经达到释放目标，可以安全继续处理其他监听者。
            continue
        except PermissionError as exc:
            # 权限不足时立即终止流程，避免启动脚本在端口仍占用时继续启动新服务。
            raise RuntimeError(f"permission denied while stopping EvalHub PID {pid}") from exc

    # 使用单调时钟避免系统时间调整影响超时，并轮询端口而非只观察旧进程状态。
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not listener_pids(port):
            return pids
        time.sleep(0.1)

    # 超时错误保留全部进程编号和端口，便于用户定位拒绝退出的旧实例。
    joined = ", ".join(str(pid) for pid in pids)
    raise RuntimeError(
        f"EvalHub PID {joined} did not release port {port} within {timeout:g}s"
    )


def positive_port(value: str) -> int:
    """把命令行文本转换为合法的 TCP 端口号。

    Args:
        value: argparse 接收到的端口文本。

    Returns:
        范围在 1 到 65535 之间的整数端口。

    Raises:
        ValueError: 输入不是整数。
        argparse.ArgumentTypeError: 整数超出 TCP 端口有效范围。
    """
    # 先使用标准整数转换保留 argparse 的原生错误展示，再校验协议允许的范围。
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def positive_timeout(value: str) -> float:
    """把命令行文本转换为严格大于零的等待秒数。

    Args:
        value: argparse 接收到的超时文本。

    Returns:
        可用于单调时钟截止时间计算的正浮点数。

    Raises:
        ValueError: 输入不是有效浮点数。
        argparse.ArgumentTypeError: 超时时间小于或等于零。
    """
    # 零或负数无法提供实际的优雅退出窗口，因此在参数解析阶段直接拒绝。
    timeout = float(value)
    if timeout <= 0:
        raise argparse.ArgumentTypeError("timeout must be greater than zero")
    return timeout


def build_parser() -> argparse.ArgumentParser:
    """构建安全停止旧 EvalHub 命令所需的参数解析器。

    Returns:
        包含主机、端口和超时选项及其校验规则的解析器。
    """
    # 默认值与本地启动脚本保持一致，使该工具也能直接从仓库根目录调用。
    parser = argparse.ArgumentParser(
        description="Safely stop an existing EvalHub that owns a local port."
    )
    # 端口和超时使用专用类型函数，在进入任何系统查询前完成输入约束检查。
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=positive_port, default=8000)
    parser.add_argument("--timeout", type=positive_timeout, default=5.0)
    return parser


def main() -> int:
    """解析命令行参数、尝试释放端口并返回适合 shell 判断的状态码。

    Returns:
        成功或无需停止时返回 0，安全检查或终止流程失败时返回 1。
    """
    # 参数解析成功后才进入带进程操作的核心函数，所有领域失败统一转换为用户提示。
    args = build_parser().parse_args()
    try:
        stopped = stop_existing_evalhub(args.host, args.port, args.timeout)
    except RuntimeError as exc:
        # 错误写入标准错误流并返回非零状态，让严格模式启动脚本立即停止后续步骤。
        print(f"Cannot start EvalHub: {exc}", file=sys.stderr)
        return 1

    # 只有确实停止旧实例时才输出提示，空闲端口保持安静以减少正常启动噪声。
    if stopped:
        joined = ", ".join(str(pid) for pid in stopped)
        print(f"Stopped previous EvalHub PID {joined} on port {args.port}")
    return 0


if __name__ == "__main__":
    # 把函数返回值交给解释器，确保调用该辅助脚本的 shell 能获得准确执行状态。
    raise SystemExit(main())
