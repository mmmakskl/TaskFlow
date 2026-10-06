from secrets import randbelow
from typing import Any
from uuid import uuid4

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from app.bot.ack import answer_callback
from app.bot.callbacks.actions import Action
from app.bot.render import render, reset
from app.models import User
from app.services.rules import DomainError
from app.services.ui import UIService


async def form(state: FSMContext, **data: Any) -> int:
    flow = randbelow(2**31 - 1) + 1
    await reset(state, flow=flow, key=uuid4().hex, **data)
    return flow


async def step(state: FSMContext, target: State) -> int:
    """Invalidate callbacks from the previous rendering while retaining the draft."""
    flow = randbelow(2**31 - 1) + 1
    await state.set_state(target)
    await state.update_data(flow=flow)
    return flow


async def show(
    screen: tuple[str, InlineKeyboardMarkup], bot: Bot, chat_id: int, state: FSMContext
) -> None:
    await render(bot, chat_id, state, *screen)


async def menu_command(
    message: Message, bot: Bot, state: FSMContext, actor: User, ui: UIService
) -> None:
    await reset(state)
    await show(await ui.menu(actor), bot, message.chat.id, state)


async def navigation(
    query: CallbackQuery,
    callback_data: Action,
    bot: Bot,
    state: FSMContext,
    actor: User,
    ui: UIService,
) -> None:
    action = callback_data
    chat_id = actor.telegram_id
    if action.name == "noop":
        await answer_callback(
            query,
        )
        return
    if action.name == "task":
        data = await state.get_data()
        screen = await ui.task_details(
            actor, action.entity, data.get("filter", "active"), data.get("page", 0)
        )
        await state.set_state(None)
    else:
        await reset(state)
        if action.name == "menu":
            screen = await ui.menu(actor)
        elif action.name == "list":
            filter_name = action.value or "active"
            await state.update_data(filter=filter_name, page=action.entity)
            screen = await ui.task_list(actor, filter_name, action.entity)
        elif action.name == "ratings":
            screen = await ui.rating(actor, action.entity)
        elif action.name == "results":
            try:
                offset = int(action.value or "0")
            except ValueError:
                raise DomainError("Недопустимая неделя.") from None
            screen = await ui.results_screen(offset)
        elif action.name == "history":
            screen = await ui.history(action.entity)
        else:
            screen = await ui.settings_screen(actor)
    await answer_callback(
        query,
    )
    await show(screen, bot, chat_id, state)


def create_router() -> Router:
    created = Router()
    created.message.register(menu_command, Command("start", "menu", "cancel"))
    created.callback_query.register(
        navigation,
        Action.filter(
            F.name.in_(
                {"menu", "list", "task", "ratings", "results", "history", "settings", "noop"}
            )
        ),
    )
    return created
