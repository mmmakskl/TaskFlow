import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.bot.ack import answer_callback
from app.config.settings import Settings
from app.repositories.base import Repository
from app.repositories.results import ResultRepository
from app.repositories.tasks import TaskRepository
from app.services.penalties import PenaltyService
from app.services.results import ResultService
from app.services.rules import DomainError
from app.services.settings import SettingsService
from app.services.tasks import TaskService
from app.services.ui import UIService

logger = logging.getLogger(__name__)


class AccessMiddleware(BaseMiddleware):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        sender = getattr(event, "from_user", None)
        message = event.message if isinstance(event, CallbackQuery) else event
        chat = getattr(message, "chat", None)
        if (
            sender is None
            or sender.id not in self.settings.authorized_ids
            or chat is None
            or chat.type != "private"
            or chat.id != sender.id
        ):
            text = "🔒 Это приватный бот.\nДоступ к нему ограничен."
            if isinstance(event, CallbackQuery):
                await answer_callback(event, text, show_alert=True)
            elif isinstance(event, Message):
                await event.answer(text)
            return None
        session = data["session"]
        repo = Repository(session)
        data.update(
            actor=await repo.user(sender.id),
            repo=repo,
            task_service=TaskService(TaskRepository(session), self.settings),
            penalty_service=PenaltyService(repo),
            result_service=ResultService(ResultRepository(session), self.settings),
            settings=self.settings,
        )
        data["ui"] = UIService(
            data["task_service"], data["penalty_service"], data["result_service"]
        )
        data["settings_service"] = SettingsService(repo)
        state: FSMContext = data["state"]
        try:
            async with session.begin_nested():
                return await handler(event, data)
        except DomainError as exc:
            if isinstance(event, CallbackQuery):
                await answer_callback(event, str(exc), show_alert=True)
            elif isinstance(event, Message):
                from app.bot.render import validation_error

                await validation_error(data["bot"], chat.id, state, str(exc))
            return None
