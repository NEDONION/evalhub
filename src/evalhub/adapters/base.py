from abc import ABC, abstractmethod


class ModelAdapter(ABC):
    """Unified interface for local models, hosted inference, and API models."""

    @abstractmethod
    def generate(self, prompt: str, **kwargs: object) -> str:
        raise NotImplementedError
