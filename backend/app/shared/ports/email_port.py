from typing import Any, Protocol


class EmailPort(Protocol):
    async def send_template(self, to: str, template_name: str, context: dict[str, Any]) -> None: ...
