from aiogram import Dispatcher, Router
from aiogram.types import CallbackQuery, Message

from app.bot.ack import answer_callback
from app.bot.handlers import common, penalties, settings, tasks
from app.bot.middlewares.access import AccessMiddleware
from app.bot.storage import PostgresStorage
from app.config.settings import Settings


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
    async def stale(query: CallbackQuery) -> None:
        await answer_callback(query, "Эта кнопка устарела. Открой /menu.", show_alert=True)

    @fallback.message()
    async def other(message: Message) -> None:
        from app.bot.keyboards.common import button, keyboard

        await message.answer(
            "Используй кнопки меню.", reply_markup=keyboard([button("🏠 Меню", "menu")])
        )

    dispatcher.include_router(fallback)
    return dispatcher
