from aiogram import Bot, Dispatcher, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.ack import answer_callback
from app.bot.handlers import common, penalties, settings, tasks
from app.bot.middlewares.access import AccessMiddleware
from app.bot.render import discard_input, render, reset, validation_error
from app.bot.storage import PostgresStorage
from app.config.settings import Settings
from app.models import User
from app.services.ui import UIService


def create_dispatcher(config: Settings) -> Dispatcher:
    dispatcher = Dispatcher(storage=PostgresStorage(), disable_fsm=False)
    dispatcher.message.middleware(AccessMiddleware(config))
    dispatcher.callback_query.middleware(AccessMiddleware(config))
    dispatcher.include_routers(
        common.create_router(),
        tasks.create_router(),
        penalties.create_router(),
        settings.create_router(),
    )
    fallback = Router()

    @fallback.callback_query()
    async def stale(
        query: CallbackQuery, bot: Bot, state: FSMContext, actor: User, ui: UIService
    ) -> None:
        await answer_callback(query, "Эта кнопка устарела. Меню обновлено.", show_alert=True)
        data = await state.get_data()
        if data.get("screen_markup"):
            await validation_error(
                bot, actor.telegram_id, state, "Используй актуальные кнопки ниже."
            )
        else:
            await reset(state)
            await render(bot, actor.telegram_id, state, *await ui.menu(actor))

    @fallback.message()
    async def other(
        message: Message, bot: Bot, state: FSMContext, actor: User, ui: UIService
    ) -> None:
        if await state.get_state():
            await validation_error(bot, actor.telegram_id, state, "Выбери действие кнопкой ниже.")
        else:
            await reset(state)
            await render(bot, actor.telegram_id, state, *await ui.menu(actor))
        await discard_input(message)

    dispatcher.include_router(fallback)
    return dispatcher
