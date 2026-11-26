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
from app.bot.render import discard_input, render, require_current, reset
from app.bot.states.forms import ConfirmationForm
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
    from app.bot.navigation import DESTINATIONS, destination_screen, remove_reply_keyboard

    await remove_reply_keyboard(bot, actor)
    await destination_screen(DESTINATIONS["menu"], bot, actor, state, ui)
    await discard_input(message)


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
        await reset(state)
        screen = await ui.task_details(
            actor, action.entity, data.get("filter", "mine_all"), data.get("page", 0)
        )
    else:
        await reset(state)
        if action.name == "menu":
            await reset(state, keep_navigation=False)
            # Put the menu at the newest end of the chat instead of editing an old screen.
            await state.update_data(message_id=None)
            screen = await ui.menu(actor)
        elif action.name == "list":
            filter_name = action.value or "mine_all"
            await state.update_data(filter=filter_name, page=action.entity)
            screen = await ui.task_list(actor, filter_name, action.entity)
        elif action.name == "ratings":
            screen = await ui.rating(actor, action.entity)
        elif action.name == "results":
            try:
                offset = int(action.value or "0")
            except ValueError:
                raise DomainError("Недопустимая неделя.") from None
            await state.update_data(results_offset=offset)
            screen = await ui.results_screen(offset)
        elif action.name == "history":
            screen = await ui.history(action.entity)
        else:
            screen = await ui.settings_screen(actor)
    await answer_callback(
        query,
    )
    await show(screen, bot, chat_id, state)


async def show_task(ui: UIService, actor: User, task_id: int, bot: Bot, state: FSMContext) -> None:
    data = await state.get_data()
    await show(
        await ui.task_details(actor, task_id, data.get("filter", "mine_all"), data.get("page", 0)),
        bot,
        actor.telegram_id,
        state,
    )


async def show_type(ui: UIService, actor: User, type_id: int, bot: Bot, state: FSMContext) -> None:
    data = await state.get_data()
    await show(
        await ui.type_details(type_id, data.get("types_page", 0)), bot, actor.telegram_id, state
    )


async def confirm_action(
    state: FSMContext, name: str, entity: int, version: int, **payload: Any
) -> Action:
    flow = await form(
        state, confirmation={"name": name, "entity": entity, "version": version, **payload}
    )
    await state.set_state(ConfirmationForm.action)
    return Action(name=name, entity=entity, version=version, value=str(flow))


async def confirmed_action(state: FSMContext, action: Action) -> dict[str, Any]:
    data = await require_current(state, ConfirmationForm.action.state)
    expected = data.get("confirmation", {})
    if action.value != str(data.get("flow")) or (action.name, action.entity, action.version) != (
        expected.get("name"),
        expected.get("entity"),
        expected.get("version"),
    ):
        raise DomainError("Это подтверждение устарело. Повтори действие.")
    return expected


def create_router() -> Router:
    created = Router()
    created.message.register(menu_command, Command("start"))
    created.callback_query.register(
        navigation,
        Action.filter(
            F.name.in_(
                {"menu", "list", "task", "ratings", "results", "history", "settings", "noop"}
            )
        ),
    )
    return created
