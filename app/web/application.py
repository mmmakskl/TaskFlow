import asyncio
import json
import logging
import secrets
from contextlib import asynccontextmanager
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from fastapi import FastAPI, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.dispatcher import create_dispatcher
from app.config.logging import configure_logging
from app.config.settings import Settings
from app.db.seed import seed
from app.db.session import database
from app.models import Inbox
from app.notifications.worker import run_workers

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings,
    bot: Bot | None = None,
    dispatcher: Dispatcher | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    workers: bool = True,
) -> FastAPI:
    configure_logging(settings)
    engine, default_factory = database(settings)
    factory = session_factory or default_factory
    telegram = bot or Bot(settings.bot_token.get_secret_value())
    dp = dispatcher or create_dispatcher(settings)
    stop = asyncio.Event()

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        logger.info("startup environment=%s", settings.environment)
        async with factory.begin() as session:
            await session.execute(text("SELECT pg_advisory_xact_lock(78421311)"))
            await seed(session, settings)
        if settings.register_webhook:
            await telegram.set_webhook(
                url=f"{settings.webhook_base_url.rstrip('/')}/telegram/webhook",
                secret_token=settings.webhook_secret.get_secret_value(),
                allowed_updates=["message", "callback_query"],
                drop_pending_updates=False,
            )
        app.state.ready = True
        task = (
            asyncio.create_task(run_workers(factory, dp, telegram, settings, stop))
            if workers
            else None
        )
        try:
            yield
        finally:
            app.state.ready = False
            stop.set()
            if task:
                try:
                    await asyncio.wait_for(task, timeout=25)
                except TimeoutError:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            # Never delete webhook on shutdown: an old Render instance may stop after a new one starts.
            await dp.storage.close()
            await telegram.session.close()
            await engine.dispose()
            logger.info("shutdown")

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ready = False

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    async def ready() -> dict[str, str]:
        if not app.state.ready:
            raise HTTPException(503, "Not ready")
        try:
            async with factory() as session:
                await session.execute(text("SELECT 1"))
        except Exception:
            logger.exception("readiness_database_failed")
            raise HTTPException(503, "Database unavailable") from None
        return {"status": "ready"}

    @app.post("/telegram/webhook")
    async def webhook(request: Request) -> dict[str, bool]:
        header = request.headers.get("x-telegram-bot-api-secret-token", "")
        if not secrets.compare_digest(header, settings.webhook_secret.get_secret_value()):
            raise HTTPException(403, "Forbidden")
        if not app.state.ready:
            raise HTTPException(503, "Not ready")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 65536:
                raise HTTPException(413, "Update too large")
        try:
            payload: dict[str, Any] = json.loads(body)
            update = Update.model_validate(payload)
        except (ValueError, TypeError, ValidationError):
            raise HTTPException(400, "Invalid Telegram update") from None
        async with factory.begin() as session:
            await session.execute(
                insert(Inbox)
                .values(update_id=update.update_id, payload=payload)
                .on_conflict_do_nothing(index_elements=[Inbox.update_id])
            )
        return {"ok": True}

    return app
