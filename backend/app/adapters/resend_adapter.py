from pathlib import Path
from typing import Any

import httpx
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import get_settings

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates" / "email"
_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)), autoescape=select_autoescape()
)


class ResendAdapter:
    """Implements EmailPort via Resend + Jinja2 templates."""

    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.resend_api_key
        self._from_email = settings.email_from

    async def send_template(self, to: str, template_name: str, context: dict[str, Any]) -> None:
        html = _jinja_env.get_template(f"{template_name}.html").render(**context)
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "from": self._from_email,
                    "to": [to],
                    "subject": context.get("subject", ""),
                    "html": html,
                },
            )
            response.raise_for_status()
