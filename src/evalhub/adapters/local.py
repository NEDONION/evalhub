from evalhub.adapters.base import ModelAdapter


class StaticMappingAdapter(ModelAdapter):
    """Deterministic adapter for demos, tests, and pipeline validation."""

    def __init__(self, responses: dict[str, str], default_response: str = "") -> None:
        self._responses = responses
        self._default_response = default_response

    def generate(self, prompt: str, **kwargs: object) -> str:
        return self._responses.get(prompt, self._default_response)
