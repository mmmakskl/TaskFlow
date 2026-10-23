"""PostgreSQL outbox delivery with at-most-once ambiguous send attempts."""

import logging
from datetime import timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError, TelegramRetryAfter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings
from app.models import Notification
from app.notifications.messages import notification_message
from app.repositories.notifications import NotificationRepository
from app.services.notifications import NotificationService

logger = logging.getLogger(__name__)


async def send_notification(
    factory: async_sessionmaker[AsyncSession], bot: Bot, settings: Settings
) -> bool:
    async with factory.begin() as session:
        repo = NotificationRepository(session)
        await repo.expire_inflight(await repo.now())
        claimed = await NotificationService(repo, settings).claim()
    if claimed is None:
        return False
    row, user, count = claimed
    status, error_code, retry_after = "sent", None, None
    try:
        message, markup = notification_message(row, count)
        if markup is None:
            await bot.send_message(user.telegram_id, message, parse_mode="HTML", request_timeout=20)
        else:
            await bot.send_message(
                user.telegram_id,
                message,
                reply_markup=markup,
                parse_mode="HTML",
                request_timeout=20,
            )
    except TelegramRetryAfter as exc:
        # Telegram explicitly rejected this request: retrying it cannot duplicate delivery.
        status, error_code = "failed", "rate_limited"
        retry_after = max(1, exc.retry_after)
    except TelegramNetworkError:
        status, error_code = "uncertain", "network_error"
    except TelegramAPIError:
        status, error_code = "failed", "telegram_rejected"
    except (ValueError, KeyError, TypeError):
        status, error_code = "failed", "invalid_payload"
    except Exception:
        # An unclassified transport error may have occurred AFTER Telegram accepted the message.
        status, error_code = "uncertain", "unexpected_error"
    async with factory.begin() as session:
        repo = NotificationRepository(session)
        current = await session.get(Notification, row.id, with_for_update=True)
        if current is not None and current.status == "sending":
            now = await repo.now()
            current.status, current.error_code, current.finished_at = status, error_code, now
            if retry_after is not None:
                if row.kind == "rating_pending":
                    # A new batch may already exist. The partial unique index coalesces it.
                    await repo.enqueue_rating(user.id, retry_after, defer_existing=True)
                elif current.attempts < 3:
                    current.status, current.finished_at = "pending", None
                    current.available_at = now + timedelta(seconds=retry_after)
            status = current.status
    logger.info(
        "notification_delivery notification_id=%s recipient_id=%s kind=%s status=%s error_code=%s",
        row.id,
        user.id,
        row.kind,
        status,
        error_code,
    )
    return True
