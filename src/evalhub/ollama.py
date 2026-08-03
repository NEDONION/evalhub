"""探测本地 Ollama 安装与服务状态，并生成控制台可用的模型选项。"""

import json
import shutil
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

# 默认连接与模型兼顾开箱即用和低资源机器的本地运行成本。
DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "qwen2.5:0.5b"

# 推荐列表提供未安装模型的展示信息，已安装模型仍会在最终选项中优先出现。
RECOMMENDED_OLLAMA_MODELS = [
    {
        "name": "qwen2.5:0.5b",
        "label": "Qwen2.5 0.5B",
        "description": "默认轻量模型，适合快速验证中文和数学任务。",
        "estimated_size_bytes": 397_000_000,
    },
    {
        "name": "qwen2.5:1.5b",
        "label": "Qwen2.5 1.5B",
        "description": "轻量中文能力更好，适合本地评测入门。",
        "estimated_size_bytes": 986_000_000,
    },
    {
        "name": "llama3.2:1b",
        "label": "Llama 3.2 1B",
        "description": "轻量英文通用模型，适合低资源机器试跑。",
        "estimated_size_bytes": 1_300_000_000,
    },
    {
        "name": "llama3.2:3b",
        "label": "Llama 3.2 3B",
        "description": "通用能力更强，本地运行成本中等。",
        "estimated_size_bytes": 2_000_000_000,
    },
    {
        "name": "deepseek-r1:1.5b",
        "label": "DeepSeek R1 1.5B",
        "description": "轻量推理模型，适合观察推理题表现。",
        "estimated_size_bytes": 1_110_000_000,
    },
    {
        "name": "phi3:mini",
        "label": "Phi-3 Mini",
        "description": "小型通用模型，适合快速本地实验。",
        "estimated_size_bytes": 2_200_000_000,
    },
]


def find_ollama_command() -> str | None:
    """查找命令行或 macOS 应用包中的 Ollama 可执行文件。

    Returns:
        可执行文件路径；两种安装方式均未发现时返回 ``None``。
    """
    # 优先尊重当前进程 PATH，兼容 Homebrew、官方安装器和自定义命令位置。
    command = shutil.which("ollama")
    if command:
        return command

    # macOS 图形应用可能未把命令加入 PATH，因此补充检查应用包内的官方路径。
    app_command = Path("/Applications/Ollama.app/Contents/Resources/ollama")
    if app_command.exists():
        return str(app_command)
    return None


def get_ollama_status(
    *,
    model: str = DEFAULT_OLLAMA_MODEL,
    base_url: str = DEFAULT_OLLAMA_BASE_URL,
) -> dict[str, object]:
    """汇总 Ollama 安装、服务、模型和推荐选项状态。

    Args:
        model: 当前控制台希望使用的目标模型标签。
        base_url: Ollama 服务根地址，路径末尾斜杠会在请求前移除。

    Returns:
        包含安装、运行、模型存在性、模型列表、选项和用户提示的字典。
    """
    # 命令不存在时无需发起网络请求，直接返回可指导安装的完整状态结构。
    command = find_ollama_command()
    if command is None:
        return {
            "installed": False,
            "running": False,
            "model_present": False,
            "command": None,
            "base_url": base_url,
            "model": model,
            "models": [],
            "model_options": _build_model_options([]),
            "message": "未检测到 ollama 命令。",
        }

    # 服务探测使用短超时读取模型标签，避免控制台状态请求长时间阻塞。
    try:
        with urlopen(f"{base_url.rstrip('/')}/api/tags", timeout=3) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        # 已安装但服务不可达与未安装是不同状态，保留命令路径和底层原因帮助排障。
        return {
            "installed": True,
            "running": False,
            "model_present": False,
            "command": command,
            "base_url": base_url,
            "model": model,
            "models": [],
            "model_options": _build_model_options([]),
            "message": f"Ollama 命令已安装，但服务未运行或不可访问：{exc}",
        }

    # 不同 Ollama 版本可能使用 ``name`` 或 ``model`` 字段，这里统一为字符串标签。
    model_items = body.get("models", [])
    models = [str(item.get("name") or item.get("model")) for item in model_items]
    actual_sizes = {
        str(item.get("name") or item.get("model")): int(item["size"])
        for item in model_items
        if (item.get("name") or item.get("model"))
        and isinstance(item.get("size"), int)
        and not isinstance(item.get("size"), bool)
    }
    model_present = model in models
    # 成功状态同时返回已安装模型和推荐补充项，供前端直接渲染同一个选择列表。
    return {
        "installed": True,
        "running": True,
        "model_present": model_present,
        "command": command,
        "base_url": base_url,
        "model": model,
        "models": models,
        "model_options": _build_model_options(models, actual_sizes),
        "message": "Ollama 已就绪。"
        if model_present
        else f"Ollama 正在运行，但未找到模型 {model}。请先执行：ollama pull {model}",
    }


def _build_model_options(
    installed_models: list[str], actual_sizes: dict[str, int] | None = None
) -> list[dict[str, object]]:
    """合并已安装模型和推荐模型为去重的展示选项。

    Args:
        installed_models: Ollama 服务按当前顺序返回的本地模型标签。

    Returns:
        已安装项优先、推荐项补齐且带安装状态的模型选项列表。
    """
    # 集合分别承担快速安装状态判断和去重，列表继续保留用户本地模型顺序。
    installed_set = set(installed_models)
    actual_sizes = actual_sizes or {}
    options: list[dict[str, object]] = []
    seen: set[str] = set()

    # 先输出本机模型；命中推荐元数据时使用友好标签，否则保留原始模型名。
    for model in installed_models:
        recommended = _recommended_by_name(model)
        options.append(
            _model_option(
                name=model,
                label=str(recommended.get("label", model)) if recommended else model,
                description=(
                    str(recommended.get("description", "本机已安装模型。"))
                    if recommended
                    else "本机已安装模型。"
                ),
                installed=True,
                recommended=recommended,
                actual_size=actual_sizes.get(model),
            )
        )
        seen.add(model)

    # 再追加尚未出现的推荐模型，让用户可以直接看到可拉取的候选项。
    for model in RECOMMENDED_OLLAMA_MODELS:
        name = str(model["name"])
        if name in seen:
            continue
        options.append(
            _model_option(
                name=name,
                label=str(model["label"]),
                description=str(model["description"]),
                installed=name in installed_set,
                recommended=model,
                actual_size=actual_sizes.get(name),
            )
        )

    # 返回全新列表，调用方的展示排序不会反向修改模块级推荐配置。
    return options


def _model_option(
    *,
    name: str,
    label: str,
    description: str,
    installed: bool,
    recommended: dict[str, object] | None,
    actual_size: int | None,
) -> dict[str, object]:
    """创建带实际或预估容量来源的统一模型选项。"""
    estimated_size = recommended.get("estimated_size_bytes") if recommended else None
    if actual_size is not None:
        size_bytes = actual_size
        size_kind = "actual"
    elif isinstance(estimated_size, int) and not isinstance(estimated_size, bool):
        size_bytes = estimated_size
        size_kind = "estimated"
    else:
        size_bytes = None
        size_kind = "unknown"
    return {
        "name": name,
        "label": label,
        "description": description,
        "installed": installed,
        "size_bytes": size_bytes,
        "size_kind": size_kind,
    }


def _recommended_by_name(name: str) -> dict[str, object] | None:
    """按模型标签查找推荐展示元数据。

    Returns:
        命中的推荐字典；模型不在推荐集合时返回 ``None``。
    """
    # 推荐集合规模很小，顺序扫描保持实现直观且不引入额外全局索引状态。
    for model in RECOMMENDED_OLLAMA_MODELS:
        if model["name"] == name:
            return model
    return None
