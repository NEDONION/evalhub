"""集中导出模型调用抽象及仓库内置的具体适配器。"""

from evalhub.adapters.base import ModelAdapter
from evalhub.adapters.local import StaticMappingAdapter
from evalhub.adapters.ollama import OllamaAdapter
from evalhub.adapters.openai_compatible import OpenAICompatibleAdapter, discover_models

# 公开面保持精简，让调用方无需了解适配器文件的内部组织方式。
__all__ = [
    "ModelAdapter",
    "OllamaAdapter",
    "OpenAICompatibleAdapter",
    "StaticMappingAdapter",
    "discover_models",
]
