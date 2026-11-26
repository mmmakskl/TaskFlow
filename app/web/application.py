import asyncio
import json
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.dispatcher import create_dispatcher
from app.bot.storage import cleanup_legacy_keyboards
from app.config.logging import configure_logging
from app.config.settings import Settings
from app.db.seed import seed
from app.db.session import database
from app.models import Inbox
from app.notifications.worker import run_workers
from app.services.rules import DomainError, StaleAction
from app.web.miniapp import configure_miniapp_menu
from app.web.miniapp import create_router as create_miniapp_router
from app.web.validation import validate_payload
from app.web.webhook import configure_webhook

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
        task = None
        try:
            logger.info("startup environment=%s", settings.environment)
            async with factory.begin() as session:
                await session.execute(text("SELECT pg_advisory_xact_lock(78421311)"))
                await seed(session, settings)
            if settings.register_webhook:
                await configure_webhook(telegram, settings)
            if settings.register_webhook and settings.webhook_base_url:
                await configure_miniapp_menu(telegram, settings)
                async with factory.begin() as session:
                    await cleanup_legacy_keyboards(session, telegram, settings)
            task = (
                asyncio.create_task(run_workers(factory, dp, telegram, settings, stop))
                if workers
                else None
            )
            app.state.worker_task = task
            app.state.ready = True
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
                except Exception:
                    logger.exception("worker_shutdown_failed")
            # Old Render replicas must never clear the new replica's webhook.
            cleanup = await asyncio.gather(
                dp.storage.close(),
                telegram.session.close(),
                engine.dispose(),
                return_exceptions=True,
            )
            if any(isinstance(result, BaseException) for result in cleanup):
                logger.error("shutdown_cleanup_failed")
            logger.info("shutdown")

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ready = False
    app.state.worker_task = None
    app.include_router(create_miniapp_router(settings, factory))
    app.mount(
        "/static",
        StaticFiles(directory=Path(__file__).parent / "static"),
        name="miniapp-static",
    )

    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
        status = 409 if isinstance(exc, StaleAction) else 422
        return JSONResponse(status_code=status, content={"detail": str(exc)})

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    async def ready() -> dict[str, str]:
        if not app.state.ready or (
            app.state.worker_task is not None and app.state.worker_task.done()
        ):
            raise HTTPException(503, "Not ready")
        try:
            async with asyncio.timeout(5):
                async with factory() as session:
                    await session.execute(text("SELECT 1"))
        except Exception:
            logger.exception("readiness_database_failed")
            raise HTTPException(503, "Database unavailable") from None
        return {"status": "ready"}

    @app.post("/telegram/webhook")
    async def webhook(request: Request) -> dict[str, bool]:
        header = request.headers.get("x-telegram-bot-api-secret-token", "")
        if not secrets.compare_digest(
            header.encode("utf-8"), settings.webhook_secret.get_secret_value().encode("ascii")
        ):
            raise HTTPException(403, "Forbidden")
        if not app.state.ready or (
            app.state.worker_task is not None and app.state.worker_task.done()
        ):
            raise HTTPException(503, "Not ready")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 65536:
                raise HTTPException(413, "Update too large")
        try:
            payload: dict[str, Any] = json.loads(body)
            validate_payload(payload)
            if (
                not isinstance(payload, dict)
                or type(payload.get("update_id")) is not int
                or not -(2**63) <= payload["update_id"] < 2**63
            ):
                raise ValueError("Invalid update ID")
            update = Update.model_validate(payload)
        except (ValueError, TypeError, ValidationError, RecursionError):
            raise HTTPException(400, "Invalid Telegram update") from None
        async with factory.begin() as session:
            await session.execute(
                insert(Inbox)
                .values(update_id=update.update_id, payload=payload)
                .on_conflict_do_nothing(index_elements=[Inbox.update_id])
            )
        return {"ok": True}

    return app
