"""探测本地 Ollama 安装与服务状态，并生成控制台可用的模型选项。"""

import json
import shutil
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from evalhub.model_protocols import model_generation_profiles

# 默认连接与模型兼顾开箱即用和低资源机器的本地运行成本。
DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "granite4.1:3b"

# 推荐列表同时服务答题与 Agent 评测；已安装模型仍会在最终选项中优先出现。
RECOMMENDED_OLLAMA_MODELS = [
    # 已在本机真实 Pi 链路验证工具调用，作为两类评测的默认轻量基线。
    {
        "name": "granite4.1:3b",
        "label": "Granite 4.1 3B",
        "description": "已验证的轻量工具调用基线，适合快速试跑。",
        "estimated_size_bytes": 2_100_000_000,
        "evaluation_types": ["model", "agent"],
        "capability_label": "Agent 基线",
    },
    # Qwen 3 小档用于观察紧凑模型能否完成工作区探索与工具编排。
    {
        "name": "qwen3:4b",
        "label": "Qwen 3 4B",
        "description": "小体积工具模型，适合低成本 Agent 与通用答题。",
        "estimated_size_bytes": 2_500_000_000,
        "evaluation_types": ["model", "agent"],
        "capability_label": "紧凑工具",
    },
    # 8B 档在容量和代码理解之间取平衡，适合作为本机主力对照。
    {
        "name": "qwen3:8b",
        "label": "Qwen 3 8B",
        "description": "平衡工具调用、中文与代码理解的通用 Agent 模型。",
        "estimated_size_bytes": 5_200_000_000,
        "evaluation_types": ["model", "agent"],
        "capability_label": "均衡 Agent",
    },
    # 14B 档提高多步推理和实现正确率，64 GB 统一内存机器仍可本地运行。
    {
        "name": "qwen3:14b",
        "label": "Qwen 3 14B",
        "description": "更高能力的工具与推理档，适合复杂答题和 Agent 任务。",
        "estimated_size_bytes": 9_300_000_000,
        "evaluation_types": ["model", "agent"],
        "capability_label": "高能力 Agent",
    },
    # Ministral 官方定位包含原生函数调用，作为非 Qwen 的平衡工具模型。
    {
        "name": "ministral-3:8b",
        "label": "Ministral 3 8B",
        "description": "原生函数调用与 Agent 工作流，适合平衡速度和任务完成率。",
        "estimated_size_bytes": 6_000_000_000,
        "evaluation_types": ["model", "agent"],
        "capability_label": "Agent 工具",
    },
    # Gemma 中档强调推理与编码，为 Coding Mini 提供更强实现能力候选。
    {
        "name": "gemma4:12b",
        "label": "Gemma 4 12B",
        "description": "面向推理、编码与 Agent 工作流的中型高能力模型。",
        "estimated_size_bytes": 7_600_000_000,
        "evaluation_types": ["model", "agent"],
        "capability_label": "Agent 编码",
    },
    # LFM 的稀疏架构用于快速连续工具调用，作为吞吐量取向的对照。
    {
        "name": "lfm2.5:8b",
        "label": "LFM 2.5 8B-A1B",
        "description": "面向本地设备的快速连续工具调用模型。",
        "estimated_size_bytes": 5_200_000_000,
        "evaluation_types": ["model", "agent"],
        "capability_label": "工具链",
    },
    # North Mini Code 专门训练终端与软件工程任务，提供容量更高的上限档。
    {
        "name": "north-mini-code-1.0:q4_K_M",
        "label": "North Mini Code 1.0",
        "description": "专门面向 Agent 编码、仓库理解和终端任务。",
        "estimated_size_bytes": 19_000_000_000,
        "evaluation_types": ["model", "agent"],
        "capability_label": "Agent 编码",
    },
    # 以下轻量模型只承担生成式答题基线，不作为 Coding Mini Agent 推荐项。
    {
        "name": "qwen2.5:0.5b",
        "label": "Qwen 2.5 0.5B",
        "description": "极小型通用答题基线，适合快速验证评测链路。",
        "estimated_size_bytes": 397_000_000,
        "evaluation_types": ["model"],
        "capability_label": "极小答题",
    },
    # 1.5B 通用档保留极低资源成本，并比 0.5B 提供更稳定的文本生成。
    {
        "name": "qwen2.5:1.5b",
        "label": "Qwen 2.5 1.5B",
        "description": "轻量通用答题模型，适合小样本与基础能力测试。",
        "estimated_size_bytes": 986_000_000,
        "evaluation_types": ["model"],
        "capability_label": "轻量答题",
    },
    # DeepSeek 小档用于补充推理型答题对照，但不承诺结构化工具调用。
    {
        "name": "deepseek-r1:1.5b",
        "label": "DeepSeek R1 1.5B",
        "description": "轻量推理答题对照，适合数学与逻辑类样本。",
        "estimated_size_bytes": 1_100_000_000,
        "evaluation_types": ["model"],
        "capability_label": "推理答题",
    },
    # 本机实测该 Ollama 模板不返回结构化工具调用，因此只保留代码答题用途。
    {
        "name": "qwen2.5-coder:7b",
        "label": "Qwen 2.5 Coder 7B",
        "description": "代码生成与修复答题模型，不用于当前 Pi Agent 链路。",
        "estimated_size_bytes": 4_700_000_000,
        "evaluation_types": ["model"],
        "capability_label": "代码答题",
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
        actual_sizes: 以模型标签为键的 Ollama 实际磁盘字节数；缺失时使用推荐预估值。

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
    """创建带实际或预估容量来源的统一模型选项。

    Args:
        name: Ollama 模型标签。
        label: 控制台显示的友好名称。
        description: 推荐用途或本地安装说明。
        installed: 模型是否已存在于当前 Ollama 服务。
        recommended: 匹配的推荐元数据；自定义本地模型为 ``None``。
        actual_size: Ollama 报告的实际磁盘字节数。

    Returns:
        包含安装状态、容量和容量来源的 JSON 兼容模型选项。
    """
    # 实际容量优先于静态推荐值，保证已安装模型展示用户机器上的真实占用。
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

    # 推荐目录控制用途与短标签；未知本机模型保持两种评测均可选择的兼容行为。
    evaluation_types = (
        list(recommended["evaluation_types"]) if recommended else ["model", "agent"]
    )
    capability_label = (
        str(recommended["capability_label"]) if recommended else "本机模型"
    )
    profile = model_generation_profiles().get(name)
    if profile is None:
        benchmark_protocol = "unsupported"
        benchmark_protocol_reason = "该模型未注册 Benchmark 生成协议。"
    elif installed:
        benchmark_protocol = "verified"
        benchmark_protocol_reason = "模型已安装且生成协议已注册，可执行正式 Benchmark。"
    else:
        benchmark_protocol = "static_only"
        benchmark_protocol_reason = "生成协议已静态校验；模型未安装，尚未执行真实验证。"

    # 容量来源和协议状态显式返回，让前端无需复制后端注册判断。
    return {
        "name": name,
        "label": label,
        "description": description,
        "installed": installed,
        "size_bytes": size_bytes,
        "size_kind": size_kind,
        "evaluation_types": evaluation_types,
        "capability_label": capability_label,
        "benchmark_protocol": benchmark_protocol,
        "benchmark_protocol_reason": benchmark_protocol_reason,
        "benchmark_protocol_version": profile.protocol_version if profile else None,
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
