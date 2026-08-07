"""在 MiniClaw 自身解释器内提供受限 describe/run JSONL 桥。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

_PREVIEW_LIMIT = 1_000
_EVENT_FIELDS = frozenset({"call_id", "tool_name", "status", "result_preview", "error_code"})


def prepare_miniclaw_import_path() -> None:
    """移除桥脚本目录，避免同名 Runner 文件遮蔽外部 MiniClaw 包。

    脚本以文件路径执行时，Python 会把 ``evalhub/agent`` 放在 ``sys.path[0]``；该目录中的
    ``miniclaw.py`` 不是外部包，必须在首次导入前移除。
    """
    bridge_directory = Path(__file__).resolve().parent
    sys.path[:] = [
        entry
        for entry in sys.path
        if Path(entry or Path.cwd()).resolve(strict=False) != bridge_directory
    ]


def normalize_event(kind: str, data: dict[str, object]) -> dict[str, object] | None:
    """把 MiniClaw 进程内事件转换为 EvalHub 白名单 Trace。

    Args:
        kind: MiniClaw ``RunEvent.kind``。
        data: MiniClaw 已结构化的事件数据。

    Returns:
        可 JSON 序列化的安全事件；模型增量和 reasoning 返回空值。
    """
    event_types = {
        "turn_started": "agent_turn_started",
        "tool_requested": "tool_requested",
        "tool_started": "tool_started",
        "tool_finished": "tool_finished",
        "approval_required": "approval_required",
        "turn_finished": "agent_message",
        "turn_failed": "agent_error",
        "turn_cancelled": "agent_error",
    }
    event_type = event_types.get(kind)
    if event_type is None:
        return None

    payload = {
        key: _safe_value(value)
        for key, value in data.items()
        if key in _EVENT_FIELDS and _safe_value(value) is not None
    }
    message = _event_message(kind, data)
    return {
        "event_type": event_type,
        "actor": "miniclaw",
        "message": message,
        "payload": payload,
    }


def _safe_value(value: object) -> object | None:
    """把白名单字段收窄为有限 JSON 标量，拒绝任意嵌套结构。"""
    if isinstance(value, str):
        return _safe_text(value)
    if isinstance(value, bool) or value is None:
        return value
    if type(value) in {int, float}:
        return value
    return None


def _safe_text(value: str) -> str:
    """移除控制字符并限制动态文本长度。"""
    cleaned = "".join(character for character in value if character >= " " or character == "\n")
    return cleaned[:_PREVIEW_LIMIT]


def _event_message(kind: str, data: dict[str, object]) -> str | None:
    """为白名单事件生成有限展示文本，不转发任意异常正文。"""
    if kind == "turn_started":
        return "MiniClaw 开始处理任务"
    if kind in {"tool_requested", "tool_started"}:
        tool_name = data.get("tool_name")
        return f"MiniClaw 调用工具 {tool_name}" if isinstance(tool_name, str) else None
    if kind == "turn_finished":
        content = data.get("content")
        return _safe_text(content) if isinstance(content, str) else "MiniClaw 已完成任务"
    if kind == "turn_failed":
        return "MiniClaw 运行失败"
    if kind == "turn_cancelled":
        return "MiniClaw 运行已取消"
    if kind == "approval_required":
        return "MiniClaw 等待人工审批"
    return None


def _emit(payload: dict[str, object]) -> None:
    """把单个协议对象写为立即刷新的 UTF-8 JSONL。"""
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)


def _load_context(workspace: Path | None = None) -> tuple[Any, Any, str, str, str]:
    """在 MiniClaw 进程内加载 Home、配置、凭据和非敏感指纹。

    Args:
        workspace: 正式运行时覆盖的 EvalHub 样本工作区。

    Returns:
        StatePaths、有效配置、API Key、包版本和运行指纹。

    Raises:
        RuntimeError: MiniClaw 尚未初始化或缺少配置指定的 API Key。
    """
    prepare_miniclaw_import_path()
    from miniclaw.config import load_config
    from miniclaw.env import load_dotenv
    from miniclaw.paths import build_state_paths, resolve_home

    load_dotenv(Path.cwd() / ".env")
    paths = build_state_paths(resolve_home())
    required = (paths.config, paths.database, paths.soul, paths.user, paths.memory_file)
    if not all(path.is_file() and not path.is_symlink() for path in required):
        raise RuntimeError("MiniClaw is not initialized")

    overrides = {"workspace": str(workspace)} if workspace is not None else None
    config = load_config(paths, overrides=overrides)
    api_key = os.environ.get(config.provider.api_key_env, "").strip()
    if not api_key:
        raise RuntimeError("MiniClaw API key is not configured")
    version = importlib.metadata.version("miniclaw")
    return paths, config, api_key, version, _runtime_fingerprint(paths, config, version)


def _runtime_fingerprint(paths: Any, config: Any, version: str) -> str:
    """摘要有效配置和上下文文件，不输出原始内容或 `.env`。

    Args:
        paths: MiniClaw ``StatePaths`` 动态边界。
        config: 已校验的 MiniClaw ``AppConfig`` 动态边界。
        version: 当前安装包版本。

    Returns:
        带算法前缀的稳定 SHA-256。
    """
    digest = hashlib.sha256()
    config_snapshot = {
        "version": version,
        "model": config.agent.model,
        "max_tool_iterations": config.agent.max_tool_iterations,
        "context_budget_tokens": config.agent.context_budget_tokens,
        "tool_result_max_chars": config.agent.tool_result_max_chars,
        "tools_enabled": list(config.tools.enabled),
        "tools_security": config.tools.security,
        "tools_ask": config.tools.ask,
    }
    digest.update(json.dumps(config_snapshot, sort_keys=True).encode("utf-8"))

    # 身份、记忆和 Skills 会改变完整 Agent 行为，只记录路径和内容摘要而不输出正文。
    roots = (paths.soul, paths.user, paths.memory_file)
    files = [path for path in roots if path.is_file() and not path.is_symlink()]
    if paths.skills.is_dir() and not paths.skills.is_symlink():
        files.extend(
            path
            for path in sorted(paths.skills.rglob("*"))
            if path.is_file() and not path.is_symlink()
        )
    for path in files:
        digest.update(str(path.relative_to(paths.home)).encode("utf-8"))
        digest.update(path.read_bytes())
    return f"sha256:{digest.hexdigest()}"


def _describe() -> int:
    """输出 MiniClaw 非敏感就绪状态，并使用稳定失败消息。"""
    try:
        _paths, config, _api_key, version, fingerprint = _load_context()
    except (OSError, RuntimeError, ValueError, sqlite3.Error):
        _emit(
            {
                "available": False,
                "version": None,
                "model": None,
                "runtime_fingerprint": None,
                "message": "MiniClaw runtime is not ready",
            }
        )
        return 0

    _emit(
        {
            "available": True,
            "version": version,
            "model": config.agent.model,
            "runtime_fingerprint": fingerprint,
            "message": "ready",
        }
    )
    return 0


async def _run(workspace: Path, conversation_id: str) -> int:
    """运行一条 stdin 任务并输出白名单事件与唯一终态。

    Args:
        workspace: EvalHub 已创建的独立样本工作区。
        conversation_id: 不与其他样本共享历史的会话标识。

    Returns:
        成功为 0；配置、审批或 Agent 错误使用非零稳定退出码。
    """
    runtime = None
    try:
        instruction = _read_instruction()
        paths, config, api_key, version, fingerprint = _load_context(workspace)
        from miniclaw.runtime import create_runtime

        runtime = create_runtime(config, paths, api_key)
        tool_call_count = 0
        approval_required = False

        async def relay(event: Any) -> None:
            """计数工具与审批事件，并立即输出标准化白名单对象。"""
            nonlocal tool_call_count, approval_required
            if event.kind == "tool_started":
                tool_call_count += 1
            if event.kind == "approval_required":
                approval_required = True
            normalized = normalize_event(str(event.kind), dict(event.data))
            if normalized is not None:
                _emit({"type": "event", "event": normalized})

        result = await runtime.service.handle(
            runtime.owner_id,
            instruction,
            conversation_id,
            on_event=relay,
        )
        if approval_required:
            _emit({"type": "error", "code": "approval_required", "message": "approval required"})
            return 3
        _emit(
            {
                "type": "result",
                "final_message": result.content,
                "tool_call_count": tool_call_count,
                "version": version,
                "model": config.agent.model,
                "runtime_fingerprint": fingerprint,
            }
        )
        return 0
    except (OSError, ValueError, sqlite3.Error) as exc:
        _emit({"type": "error", "code": "executor_not_ready", "message": type(exc).__name__})
        return 2
    except RuntimeError as exc:
        code = "provider_error" if "Provider" in type(exc).__name__ else "agent_error"
        _emit({"type": "error", "code": code, "message": type(exc).__name__})
        return 4
    finally:
        if runtime is not None:
            await runtime.aclose()


def _read_instruction() -> str:
    """读取 stdin 的单个公开任务对象并拒绝空值。

    Raises:
        ValueError: stdin 不是对象或 instruction 不是非空字符串。
    """
    try:
        payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError as exc:
        raise ValueError("invalid instruction payload") from exc
    if not isinstance(payload, dict):
        raise ValueError("instruction payload must be an object")
    instruction = payload.get("instruction")
    if not isinstance(instruction, str) or not instruction.strip():
        raise ValueError("instruction must be a non-empty string")
    return instruction


def _parser() -> argparse.ArgumentParser:
    """构造只允许 describe 和 run 的固定命令行解析器。"""
    parser = argparse.ArgumentParser(description="EvalHub MiniClaw bridge")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("describe")
    run = subparsers.add_parser("run")
    run.add_argument("--workspace", type=Path, required=True)
    run.add_argument("--conversation-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """执行固定桥命令，并返回适合父进程分类的退出码。"""
    arguments = _parser().parse_args(argv)
    if arguments.command == "describe":
        return _describe()
    workspace = arguments.workspace.resolve(strict=False)
    if not workspace.is_dir():
        _emit({"type": "error", "code": "executor_not_ready", "message": "workspace missing"})
        return 2
    return asyncio.run(_run(workspace, str(arguments.conversation_id)))


if __name__ == "__main__":
    raise SystemExit(main())
