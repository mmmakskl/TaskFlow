from datetime import datetime, timedelta

from app.config.settings import Settings
from app.models import Notification, User
from app.repositories.base import Repository
from app.repositories.notifications import NotificationRepository
from app.utils.time import utcnow


def cooldown_ready(last: datetime | None, now: datetime, minutes: int) -> bool:
    return last is None or now - last >= timedelta(minutes=minutes)


class NotificationService:
    def __init__(self, repo: Repository, settings: Settings) -> None:
        self.repo = (
            repo
            if isinstance(repo, NotificationRepository)
            else NotificationRepository(repo.session)
        )
        self.settings = settings

    async def claim(self) -> tuple[Notification, User, int] | None:
        now = utcnow()
        notification = await self.repo.next_due(now)
        if notification is None:
            return None
        user = await self.repo.get(User, notification.recipient_user_id, lock=True)
        if user.telegram_id not in self.settings.authorized_ids:
            notification.status = "skipped"
            return None
        count = 0
        if notification.kind == "rating_pending":
            count = await self.repo.pending_count(user.id)
            if count == 0 or not cooldown_ready(
                user.last_rating_notification_at,
                now,
                self.settings.rating_notification_cooldown_minutes,
            ):
                notification.status = "skipped"
                return None
            user.last_rating_notification_at = now
        # Commit before contacting Telegram. An ambiguous network failure is NOT retried:
        # Telegram sendMessage has no idempotency key; this trades possible omission for no spam.
        notification.status = "sending"
        await self.repo.flush()
        return notification, user, count
