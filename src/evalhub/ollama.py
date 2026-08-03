import json
from pathlib import Path
import shutil
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "qwen2.5:0.5b"
RECOMMENDED_OLLAMA_MODELS = [
    {
        "name": "qwen2.5:0.5b",
        "label": "Qwen2.5 0.5B",
        "description": "默认轻量模型，适合快速验证中文和数学任务。",
    },
    {
        "name": "qwen2.5:1.5b",
        "label": "Qwen2.5 1.5B",
        "description": "轻量中文能力更好，适合本地评测入门。",
    },
    {
        "name": "llama3.2:1b",
        "label": "Llama 3.2 1B",
        "description": "轻量英文通用模型，适合低资源机器试跑。",
    },
    {
        "name": "llama3.2:3b",
        "label": "Llama 3.2 3B",
        "description": "通用能力更强，本地运行成本中等。",
    },
    {
        "name": "deepseek-r1:1.5b",
        "label": "DeepSeek R1 1.5B",
        "description": "轻量推理模型，适合观察推理题表现。",
    },
    {
        "name": "phi3:mini",
        "label": "Phi-3 Mini",
        "description": "小型通用模型，适合快速本地实验。",
    },
]


def find_ollama_command() -> str | None:
    command = shutil.which("ollama")
    if command:
        return command

    app_command = Path("/Applications/Ollama.app/Contents/Resources/ollama")
    if app_command.exists():
        return str(app_command)
    return None


def get_ollama_status(
    *,
    model: str = DEFAULT_OLLAMA_MODEL,
    base_url: str = DEFAULT_OLLAMA_BASE_URL,
) -> dict[str, object]:
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

    try:
        with urlopen(f"{base_url.rstrip('/')}/api/tags", timeout=3) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
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

    models = [str(item.get("name") or item.get("model")) for item in body.get("models", [])]
    model_present = model in models
    return {
        "installed": True,
        "running": True,
        "model_present": model_present,
        "command": command,
        "base_url": base_url,
        "model": model,
        "models": models,
        "model_options": _build_model_options(models),
        "message": "Ollama 已就绪。"
        if model_present
        else f"Ollama 正在运行，但未找到模型 {model}。请先执行：ollama pull {model}",
    }


def _build_model_options(installed_models: list[str]) -> list[dict[str, object]]:
    installed_set = set(installed_models)
    options: list[dict[str, object]] = []
    seen: set[str] = set()

    for model in installed_models:
        recommended = _recommended_by_name(model)
        options.append(
            {
                "name": model,
                "label": recommended.get("label", model) if recommended else model,
                "description": recommended.get("description", "本机已安装模型。")
                if recommended
                else "本机已安装模型。",
                "installed": True,
            }
        )
        seen.add(model)

    for model in RECOMMENDED_OLLAMA_MODELS:
        name = str(model["name"])
        if name in seen:
            continue
        options.append(
            {
                "name": name,
                "label": model["label"],
                "description": model["description"],
                "installed": name in installed_set,
            }
        )

    return options


def _recommended_by_name(name: str) -> dict[str, str] | None:
    for model in RECOMMENDED_OLLAMA_MODELS:
        if model["name"] == name:
            return model
    return None
