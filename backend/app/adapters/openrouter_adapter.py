from collections.abc import AsyncIterator
from typing import TypeVar

import httpx
from pydantic import BaseModel

from app.config import get_settings

T = TypeVar("T", bound=BaseModel)

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterAdapter:
    """Implements LLMPort (app/shared/ports/llm_port.py) via OpenRouter → Claude."""

    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.openrouter_api_key
        self._model = settings.openrouter_model

    async def generate_structured(
        self, prompt: str, response_model: type[T], *, system: str | None = None
    ) -> T:
        messages = [{"role": "user", "content": prompt}]
        if system:
            messages.insert(0, {"role": "system", "content": system})

        async with httpx.AsyncClient() as client:
            response = await client.post(
                _OPENROUTER_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self._model,
                    "messages": messages,
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": response_model.__name__,
                            "schema": response_model.model_json_schema(),
                        },
                    },
                },
                timeout=60,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            return response_model.model_validate_json(content)

    async def stream_text(self, prompt: str, *, system: str | None = None) -> AsyncIterator[str]:
        messages = [{"role": "user", "content": prompt}]
        if system:
            messages.insert(0, {"role": "system", "content": system})

        async with (
            httpx.AsyncClient() as client,
            client.stream(
                "POST",
                _OPENROUTER_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"model": self._model, "messages": messages, "stream": True},
                timeout=60,
            ) as response,
        ):
            async for line in response.aiter_lines():
                if line.startswith("data: ") and line != "data: [DONE]":
                    yield line.removeprefix("data: ")
