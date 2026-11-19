import json
import logging
from typing import Any

import httpx

from app.config import Settings, get_settings, validate_llm_endpoint
from app.generation.providers.base import (
    InvalidProviderResponseError,
    ProviderResponse,
    ProviderUnavailableError,
)

logger = logging.getLogger(__name__)


class OpenAICompatibleProvider:
    """Synchronous chat-completions adapter. Never logs prompts or credentials."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def generate(self, *, system_message: str, user_message: str) -> ProviderResponse:
        validate_llm_endpoint(self.settings.llm_base_url, self.settings.app_env)
        secret = self.settings.llm_api_key
        if secret is None or not secret.get_secret_value().strip():
            raise ProviderUnavailableError("LLM provider is not configured")
        endpoint = f"{self.settings.llm_base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {secret.get_secret_value()}", "Content-Type": "application/json"}
        body = {
            "model": self.settings.llm_model,
            "temperature": self.settings.llm_temperature,
            "max_tokens": min(self.settings.llm_max_output_tokens, self.settings.rag_max_answer_tokens),
            "messages": [{"role": "system", "content": system_message},
                         {"role": "user", "content": user_message}],
        }
        for attempt in range(2):
            try:
                with httpx.Client(timeout=self.settings.llm_timeout_seconds, follow_redirects=False) as client:
                    response = client.post(endpoint, headers=headers, json=body)
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt == 0:
                        continue
                    raise ProviderUnavailableError("LLM provider is unavailable")
                response.raise_for_status()
                parsed = self._parse_response(response.json())
                return parsed
            except ProviderUnavailableError:
                raise
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                logger.warning("LLM request failed (%s)", type(exc).__name__)
                if attempt == 0:
                    continue
                raise ProviderUnavailableError("LLM provider is unavailable") from exc
            except httpx.HTTPStatusError as exc:
                # Do not log response body: compatible providers may echo sensitive data.
                logger.warning("LLM provider rejected a request (status=%s)", exc.response.status_code)
                raise ProviderUnavailableError("LLM provider is unavailable") from exc
            except httpx.HTTPError as exc:
                logger.warning("LLM transport failed (%s)", type(exc).__name__)
                if attempt == 0:
                    continue
                raise ProviderUnavailableError("LLM provider is unavailable") from exc
            except (ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
                logger.warning("LLM provider returned an invalid response (%s)", type(exc).__name__)
                raise InvalidProviderResponseError("LLM provider returned an invalid response") from exc
        raise ProviderUnavailableError("LLM provider is unavailable")

    @staticmethod
    def _parse_response(data: Any) -> ProviderResponse:
        content = data["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Empty provider output")
        payload = json.loads(content)
        if not isinstance(payload, dict) or set(payload) != {"answer", "citations", "insufficient_information"}:
            raise ValueError("Unexpected response shape")
        answer, citations, insufficient = payload["answer"], payload["citations"], payload["insufficient_information"]
        if not isinstance(answer, str) or not isinstance(citations, list) or not isinstance(insufficient, bool):
            raise ValueError("Unexpected response values")
        if any(not isinstance(item, str) for item in citations):
            raise ValueError("Invalid citation labels")
        return ProviderResponse(answer, citations, insufficient)
