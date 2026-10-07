import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.bot.ack import answer_callback
from app.bot.middlewares.interaction import (
    adopt_entry_message,
    callback_warning,
    draft_valid,
    input_warning,
)
from app.bot.navigation import handle_navigation
from app.bot.render import discard_input, render, reset, validation_error
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
            try:
                if isinstance(event, CallbackQuery):
                    await answer_callback(event, text, show_alert=True)
                elif isinstance(event, Message):
                    await event.answer(text)
            except (TelegramAPIError, TelegramNetworkError):
                # A denied sender can block the bot immediately after messaging it.
                # Never retry that courtesy reply ahead of authorized users' work.
                logger.debug("access_denial_delivery_failed")
            return None
        session = data["session"]
        repo = Repository(session)
        data.update(
            actor=await repo.user(sender.id),
            repo=repo,
            task_service=TaskService(TaskRepository(session), self.settings),
            penalty_service=PenaltyService(repo, self.settings),
            result_service=ResultService(ResultRepository(session), self.settings),
            settings=self.settings,
        )
        data["ui"] = UIService(
            data["task_service"], data["penalty_service"], data["result_service"]
        )
        data["settings_service"] = SettingsService(repo, self.settings)
        state: FSMContext = data["state"]
        try:
            async with session.begin_nested():
                if isinstance(event, (Message, CallbackQuery)) and await handle_navigation(
                    event, data
                ):
                    return None
        except DomainError as exc:
            if isinstance(event, CallbackQuery):
                await answer_callback(event, str(exc), show_alert=True)
            elif isinstance(event, Message):
                await validation_error(data["bot"], chat.id, state, str(exc))
                await discard_input(event)
            return None
        current = await state.get_state()
        if not draft_valid(current, await state.get_data()):
            await reset(state, keep_navigation=False)
            text, markup = await data["ui"].menu(data["actor"])
            await render(
                data["bot"],
                chat.id,
                state,
                "♻️ Черновик сброшен. Начни действие заново.\n\n" + text,
                markup,
            )
            if isinstance(event, CallbackQuery):
                await answer_callback(event, "Меню восстановлено.")
            return None
        warning = (
            await callback_warning(event, state)
            if isinstance(event, CallbackQuery)
            else await input_warning(event, state, known_command=bool(data.get("command")))
            if isinstance(event, Message)
            else None
        )
        if warning:
            if isinstance(event, CallbackQuery):
                await answer_callback(event, warning, show_alert=True)
            elif isinstance(event, Message):
                if not (await state.get_data()).get("screen_markup"):
                    await render(data["bot"], chat.id, state, *await data["ui"].menu(data["actor"]))
                await validation_error(data["bot"], chat.id, state, warning)
                await discard_input(event)
            return None
        adopted = (
            await adopt_entry_message(event, data["bot"], state)
            if isinstance(event, CallbackQuery)
            else False
        )
        try:
            async with session.begin_nested():
                return await handler(event, data)
        except DomainError as exc:
            if isinstance(event, CallbackQuery):
                await answer_callback(event, str(exc), show_alert=True)
                if adopted:
                    await reset(state)
                    await render(data["bot"], chat.id, state, *await data["ui"].menu(data["actor"]))
            elif isinstance(event, Message):
                await validation_error(data["bot"], chat.id, state, str(exc))
                await discard_input(event)
            return None
