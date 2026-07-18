from collections.abc import AsyncIterator
from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMPort(Protocol):
    async def generate_structured(
        self, prompt: str, response_model: type[T], *, system: str | None = None
    ) -> T: ...

    def stream_text(self, prompt: str, *, system: str | None = None) -> AsyncIterator[str]: ...
