from datetime import datetime, timedelta

from app.config.settings import Settings
from app.models import Notification, User
from app.repositories.base import Repository
from app.repositories.notifications import NotificationRepository


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
        now = await self.repo.now()
        notification = await self.repo.next_due(now)
        if notification is None:
            return None
        user = await self.repo.recipient(notification.recipient_user_id)
        now = await self.repo.now()
        if user.telegram_id not in self.settings.authorized_ids:
            notification.status = "skipped"
            notification.finished_at = now
            notification.error_code = "unauthorized"
            return None
        count = 0
        if notification.kind == "rating_pending":
            count = await self.repo.pending_count(user.id)
            if count == 0:
                notification.status = "skipped"
                notification.finished_at = now
                return None
            if not cooldown_ready(
                user.last_rating_notification_at,
                now,
                self.settings.rating_notification_cooldown_minutes,
            ):
                assert user.last_rating_notification_at is not None
                notification.available_at = user.last_rating_notification_at + timedelta(
                    minutes=self.settings.rating_notification_cooldown_minutes
                )
                return None
            user.last_rating_notification_at = now
        # Commit before contacting Telegram. An ambiguous network failure is NOT retried:
        # Telegram sendMessage has no idempotency key; this trades possible omission for no spam.
        notification.status = "sending"
        notification.claimed_at = now
        notification.attempts += 1
        notification.error_code = None
        await self.repo.flush()
        return notification, user, count
