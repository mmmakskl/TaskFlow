import asyncio
import logging
from datetime import timedelta

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.storage import current_session
from app.config.settings import Settings
from app.models import Inbox, Notification, User
from app.notifications.delivery import send_notification
from app.utils.time import utcnow

logger = logging.getLogger(__name__)


async def process_update(
    factory: async_sessionmaker[AsyncSession], dispatcher: Dispatcher, bot: Bot
) -> bool:
    update_id = None
    try:
        async with factory.begin() as session:
            # Two-user app: one short transaction at a time across replicas. This also serializes FSM.
            await session.execute(text("SELECT pg_advisory_xact_lock(78421310)"))
            record = await session.scalar(
                select(Inbox)
                .where(Inbox.status == "pending")
                .order_by(Inbox.update_id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if record is None or record.available_at > utcnow():
                return False
            update_id = record.update_id
            token = current_session.set(session)
            try:
                await dispatcher.feed_update(
                    bot,
                    Update.model_validate(record.payload, context={"bot": bot}),
                    session=session,
                )
            finally:
                current_session.reset(token)
            record.status = "done"
            record.payload = {}  # Minimize retained private message content.
            logger.info("update_processed update_id=%s", update_id)
        return True
    except Exception:
        logger.exception("update_failed update_id=%s", update_id)
        if update_id is not None:
            async with factory.begin() as session:
                record = await session.get(Inbox, update_id, with_for_update=True)
                if record is not None and record.status == "pending":
                    record.attempts += 1
                    if record.attempts == 1:
                        update = Update.model_validate(record.payload)
                        sender = (
                            update.message.from_user
                            if update.message
                            else (
                                update.callback_query.from_user if update.callback_query else None
                            )
                        )
                        chat = (
                            update.message.chat
                            if update.message
                            else (
                                update.callback_query.message.chat
                                if update.callback_query and update.callback_query.message
                                else None
                            )
                        )
                        user = (
                            await session.scalar(select(User).where(User.telegram_id == sender.id))
                            if sender
                            else None
                        )
                        if user and chat and chat.type == "private" and chat.id == user.telegram_id:
                            session.add(
                                Notification(
                                    recipient_user_id=user.id,
                                    kind="handler_error",
                                    payload={},
                                    dedupe_key=f"error:{update_id}",
                                    available_at=utcnow(),
                                )
                            )
                    record.available_at = utcnow() + timedelta(seconds=min(60, 2**record.attempts))
                    if record.attempts >= 5:
                        record.status = "failed"
                        # No user data appears in the log. Operator sees update ID.
                        logger.error("update_dead_letter update_id=%s", update_id)
        return False


async def run_workers(
    factory: async_sessionmaker[AsyncSession],
    dispatcher: Dispatcher,
    bot: Bot,
    settings: Settings,
    stop: asyncio.Event,
) -> None:
    await asyncio.gather(
        run_updates(factory, dispatcher, bot, stop),
        run_notifications(factory, bot, settings, stop),
    )


async def run_updates(
    factory: async_sessionmaker[AsyncSession], dispatcher: Dispatcher, bot: Bot, stop: asyncio.Event
) -> None:
    cleanup_at = utcnow()
    while not stop.is_set():
        try:
            worked = await process_update(factory, dispatcher, bot)
            if utcnow() >= cleanup_at:
                async with factory.begin() as session:
                    await session.execute(
                        delete(Inbox).where(
                            Inbox.status == "done", Inbox.created_at < utcnow() - timedelta(days=7)
                        )
                    )
                cleanup_at = utcnow() + timedelta(hours=1)
            if not worked:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=0.25)
                except TimeoutError:
                    pass
        except Exception:
            logger.exception("worker_failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=1)
            except TimeoutError:
                pass


async def run_notifications(
    factory: async_sessionmaker[AsyncSession], bot: Bot, settings: Settings, stop: asyncio.Event
) -> None:
    while not stop.is_set():
        try:
            worked = await send_notification(factory, bot, settings)
        except Exception:
            logger.exception("notification_worker_failed")
            worked = False
        if not worked:
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.25)
            except TimeoutError:
                pass
