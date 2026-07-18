from uuid import UUID

from app.modules.notifications.repository import NotificationRepository
from app.shared.ports.email_port import EmailPort


class NotificationService:
    def __init__(self, repository: NotificationRepository, email: EmailPort):
        self._repository = repository
        self._email = email

    async def send_welcome_email(self, user_id: UUID, to: str) -> None:
        await self._email.send_template(
            to, "welcome", {"subject": "Welcome to Creator Intelligence"}
        )
        await self._repository.log(user_id=user_id, template_name="welcome")

    async def send_weekly_briefing(self, user_id: UUID, to: str, context: dict) -> None:
        await self._email.send_template(
            to, "weekly_briefing", {"subject": "Your weekly briefing", **context}
        )
        await self._repository.log(user_id=user_id, template_name="weekly_briefing")
