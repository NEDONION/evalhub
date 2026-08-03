from evalhub.adapters.base import ModelAdapter
from evalhub.adapters.local import StaticMappingAdapter
from evalhub.adapters.ollama import OllamaAdapter

__all__ = ["ModelAdapter", "OllamaAdapter", "StaticMappingAdapter"]
