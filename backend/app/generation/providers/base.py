from dataclasses import dataclass
from typing import Protocol


class ProviderUnavailableError(RuntimeError):
    """LLM provider is not configured or unavailable."""


class InvalidProviderResponseError(RuntimeError):
    """LLM provider returned an invalid response."""


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    answer: str
    citations: list[str]
    insufficient_information: bool


class GenerationProvider(Protocol):
    def generate(self, *, system_message: str, user_message: str) -> ProviderResponse: ...
