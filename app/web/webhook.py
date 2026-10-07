"""Idempotent webhook registration with bounded transient-error retries."""

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter, TelegramServerError

from app.config.settings import Settings

logger = logging.getLogger(__name__)


async def configure_webhook(bot: Bot, settings: Settings) -> None:
    for attempt in range(3):
        try:
            # Always set it: getWebhookInfo cannot report whether its secret matches ENV.
            configured = await bot.set_webhook(
                url=f"{settings.webhook_base_url.rstrip('/')}/telegram/webhook",
                secret_token=settings.webhook_secret.get_secret_value(),
                allowed_updates=["message", "callback_query"],
                max_connections=5,
                drop_pending_updates=False,
                request_timeout=10,
            )
            if not configured:
                raise RuntimeError("Telegram did not confirm webhook registration")
            logger.info("webhook_configured")
            return
        except (TelegramNetworkError, TelegramServerError, TelegramRetryAfter) as exc:
            delay = exc.retry_after if isinstance(exc, TelegramRetryAfter) else 2**attempt
            if attempt == 2 or delay > 10:
                logger.error("webhook_configuration_failed attempt=%s", attempt + 1)
                raise
            logger.warning("webhook_configuration_retry attempt=%s", attempt + 1)
            await asyncio.sleep(delay)
