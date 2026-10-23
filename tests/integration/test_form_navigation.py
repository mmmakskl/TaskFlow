import pytest
from aiogram.methods import SendMessage
from aiogram.types import ReplyKeyboardMarkup
from sqlalchemy import func, select

from app.models import AuditEvent, Penalty, PenaltyType, ScoreEvent, Task, User
from app.repositories.tasks import TaskRepository
from app.services.rules import DomainError
from app.services.tasks import TaskService
from tests.integration.test_web_and_bot import BotDriver

pytestmark = pytest.mark.integration


def data_for(driver, user_id, label):
    return next(
        b.callback_data
        for row in driver.screen(user_id).reply_markup.inline_keyboard
        for b in row
        if b.text == label
    )


async def test_task_estimate_back_edit_clear_and_peer_visibility(factory, settings):
    driver = BotDriver(factory, settings)
    await driver.text(1001, "/menu")
    await driver.press(1001, "➕ Задача")
    await driver.press(1001, "Себе")
    await driver.text(1001, "Сделать <план>")
    await driver.press(1001, "Название")
    await driver.press(1001, "⬅️ Назад")
    assert "Сделать &lt;план&gt;" in driver.screen(1001).text
    await driver.press(1001, "Дедлайн")
    await driver.press(1001, "Выбрать дату")
    await driver.press(1001, "⬅️ Назад")
    await driver.press(1001, "⬅️ Назад")
    await driver.press(1001, "⏱ Время")
    old_choice = data_for(driver, 1001, "30 мин")
    await driver.press(1001, "30 мин")
    await driver.press(1001, "⏱ Время")
    await driver.press(1001, data=old_choice)
    assert "Сколько времени" in driver.screen(1001).text
    await driver.text(1001, "95")
    await driver.press(1001, "Комментарий")
    await driver.press(1001, "Добавить комментарий")
    await driver.press(1001, "⬅️ Назад")
    await driver.press(1001, "Пропустить")
    create_callback = await driver.press(1001, "✅ Создать")
    await driver.press(1001, data=create_callback)
    assert "~1 ч 35 мин" in driver.screen(1001).text
    await driver.text(1002, "/menu")
    await driver.press(1002, "Входящие")
    assert "План: ~1 ч 35 мин" in driver.screen(1002).text
    await driver.press(1001, "К задачам")
    assert "~1 ч 35 мин" in driver.screen(1001).text
    await driver.press(1001, "Сделать")
    await driver.press(1001, "Изменить")
    await driver.press(1001, "Оценка времени")
    await driver.press(1001, "⬅️ Назад")
    assert "Что изменить" in driver.screen(1001).text
    await driver.press(1001, "Оценка времени")
    await driver.press(1001, "2 ч")
    assert "~2 ч" in driver.screen(1001).text
    await driver.press(1001, "Изменить")
    await driver.press(1001, "Дедлайн")
    await driver.press(1001, "⬅️ Назад")
    assert "Что изменить" in driver.screen(1001).text
    await driver.press(1001, "Оценка времени")
    await driver.press(1001, "Не указывать")
    assert "План: не указан" in driver.screen(1001).text
    async with factory() as session:
        task = await session.scalar(select(Task))
        assert task.estimated_minutes is None
        assert await session.scalar(select(func.count(Task.id))) == 1
        assert await session.scalar(select(func.count(ScoreEvent.id))) == 0
        event = await session.scalar(
            select(AuditEvent).where(AuditEvent.action == "edit").order_by(AuditEvent.id.desc())
        )
        assert (
            event.old_data["estimated_minutes"] == 120
            and event.new_data["estimated_minutes"] is None
        )
    sends = [
        call
        for call in driver.api.calls
        if isinstance(call, SendMessage) and not isinstance(call.reply_markup, ReplyKeyboardMarkup)
    ]
    assert len(sends) == 2
    await driver.bot.session.close()


@pytest.mark.parametrize("bad", ["0", "10081", "abc", "-15", "1.5"])
async def test_invalid_estimate_keeps_form(factory, settings, bad):
    driver = BotDriver(factory, settings)
    await driver.text(1001, "/menu")
    await driver.press(1001, "➕ Задача")
    await driver.press(1001, "Себе")
    await driver.text(1001, "Estimate")
    await driver.press(1001, "⏱ Время")
    await driver.text(1001, bad)
    await driver.press(1001, "15 мин")
    await driver.press(1001, "✅ Создать")
    async with factory() as session:
        assert (await session.scalar(select(Task))).estimated_minutes == 15
    await driver.bot.session.close()


async def test_penalty_all_back_steps_presets_stale_confirmation_and_no_early_save(
    factory, settings
):
    driver = BotDriver(factory, settings)
    await driver.text(1002, "/menu")
    await driver.press(1002, "⚠️ Штраф")
    await driver.press(1002, "Макс")
    await driver.press(1002, "⬅️ Назад")
    assert "Кому?" in driver.screen(1002).text
    await driver.press(1002, "Вика")
    await driver.press(1002, "Instagram")
    await driver.press(1002, "⬅️ Назад")
    await driver.press(1002, "Instagram")
    await driver.press(1002, "15 мин")
    await driver.press(1002, "⬅️ Назад")
    await driver.press(1002, "5 мин")
    await driver.press(1002, "Комментарий")
    await driver.press(1002, "⬅️ Назад")
    await driver.press(1002, "Комментарий")
    await driver.text(1002, "Пояснение")
    stale_confirm = data_for(driver, 1002, "✅ Назначить")
    await driver.press(1002, "⬅️ Назад")
    assert "Пояснение" in driver.screen(1002).text
    await driver.press(1002, "Пропустить")
    assert "Пояснение" not in driver.screen(1002).text
    await driver.press(1002, data=stale_confirm)
    async with factory() as session:
        assert await session.scalar(select(func.count(Penalty.id))) == 0
    confirm = await driver.press(1002, "Назначить")
    await driver.press(1002, data=confirm)
    async with factory() as session:
        penalty = await session.scalar(select(Penalty))
        vika = await session.scalar(select(User).where(User.role == "vika"))
        assert penalty.target_user_id == vika.id
        assert penalty.quantity == 5 and penalty.points == 500 and penalty.comment is None
        assert await session.scalar(select(func.count(Penalty.id))) == 1
    await driver.bot.session.close()


async def test_timezone_presets_manual_input_back_and_weekly_target(factory, settings):
    driver = BotDriver(factory, settings)
    await driver.text(1001, "/menu")
    await driver.press(1001, "Настройки")
    await driver.press(1001, "Часовой пояс")
    assert "Europe/Moscow" in driver.screen(1001).text
    old = data_for(
        driver,
        1001,
        next(
            b.text
            for row in driver.screen(1001).reply_markup.inline_keyboard
            for b in row
            if "Москва" in b.text
        ),
    )
    await driver.press(1001, "⬅️ Настройки")
    await driver.press(1001, "Часовой пояс")
    await driver.press(1001, data=old)
    assert "Часовой пояс дат" in driver.screen(1001).text
    await driver.press(1001, "Москва")
    assert "Настройки" in driver.screen(1001).text and "Europe/Moscow" in driver.screen(1001).text
    await driver.press(1001, "Недельная цель")
    await driver.press(1001, "15 000")
    assert "15 000" in driver.screen(1001).text
    await driver.press(1001, "Часовой пояс")
    await driver.text(1001, "Invalid/Zone")
    await driver.text(1001, "Europe/Berlin")
    assert "Europe/Berlin" in driver.screen(1001).text
    async with factory() as session:
        max_user = await session.scalar(select(User).where(User.role == "max"))
        assert max_user.timezone == "Europe/Berlin" and max_user.weekly_target == 15000
    await driver.bot.session.close()


async def test_type_wizard_back_skip_optional_description_and_rate_preset(factory, settings):
    driver = BotDriver(factory, settings)
    await driver.text(1001, "/menu")
    await driver.press(1001, "Настройки")
    await driver.press(1001, "Типы штрафов")
    await driver.press(1001, "Новый тип")
    await driver.text(1001, "Тестовый тип")
    await driver.press(1001, "⬅️ Назад")
    await driver.press(1001, "Оставить значение")
    await driver.text(1001, "⚠️")
    await driver.press(1001, "⬅️ Назад")
    await driver.press(1001, "Оставить значение")
    await driver.press(1001, "Пропустить")
    await driver.press(1001, "За минуту")
    await driver.press(1001, "⬅️ Назад")
    await driver.press(1001, "⬅️ Назад")
    await driver.press(1001, "Пропустить")
    await driver.press(1001, "За минуту")
    await driver.press(1001, "50")
    async with factory() as session:
        kind = await session.scalar(select(PenaltyType).where(PenaltyType.name == "Тестовый тип"))
        assert kind.rate == 50 and kind.description == ""
    await driver.press(1001, "Способ расчёта")
    await driver.press(1001, "⬅️ Назад")
    assert "Тестовый тип" in driver.screen(1001).text
    await driver.bot.session.close()


async def test_estimate_edit_permissions_and_completion_snapshot(factory, settings):
    async with factory.begin() as session:
        users = list((await session.scalars(select(User).order_by(User.id))).all())
        max_user, vika = users
        service = TaskService(TaskRepository(session), settings)
        task = await service.create(max_user, "Time planning", "estimate-key", estimate=90)
        await service.rate(vika, task.id, 500, task.version)
        await service.edit(max_user, task.id, task.version, estimated_minutes=120)
        assert task.status == "BACKLOG"
        await service.rate(vika, task.id, 500, task.version)
        await service.change_status(max_user, task.id, "DONE", task.version)
        assert task.awarded_points == 500 and task.estimated_minutes == 120
        with pytest.raises(DomainError):
            await service.edit(vika, task.id, task.version, estimated_minutes=15)
        with pytest.raises(DomainError):
            await service.edit(max_user, task.id, task.version, estimated_minutes=15)
        assert await session.scalar(select(func.sum(ScoreEvent.points))) == 500


async def test_upgrade_from_previous_schema_preserves_existing_task(factory, settings):
    import asyncio
    import os
    import sys

    from sqlalchemy import text

    from tests.integration.test_business_migrations import alembic

    async with factory.begin() as session:
        await session.execute(text("DROP SCHEMA taskflow CASCADE"))
        await session.execute(text("DROP TABLE public.alembic_version"))
    code, output = await alembic(settings, "upgrade", "0001")
    assert code == 0, output
    async with factory.begin() as session:
        await session.execute(
            text(
                "INSERT INTO taskflow.users (id,telegram_id,display_name,role,timezone,weekly_target,created_at,updated_at) VALUES (1,1001,'Max','max','UTC',10000,now(),now()),(2,1002,'Vika','vika','UTC',10000,now(),now())"
            )
        )
        await session.execute(
            text(
                "INSERT INTO taskflow.tasks (owner_user_id,evaluator_user_id,title,status,operation_key,version,created_at,updated_at) SELECT m.id,v.id,'Existing task','BACKLOG','legacy',1,now(),now() FROM taskflow.users m CROSS JOIN taskflow.users v WHERE m.role='max' AND v.role='vika'"
            )
        )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.db.migrate",
        env={**os.environ, "DATABASE_URL": settings.database_url.get_secret_value()},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    assert process.returncode == 0, (stdout + stderr).decode()
    async with factory() as session:
        task = await session.scalar(select(Task))
        assert task.title == "Existing task" and task.estimated_minutes is None
        assert (
            await session.scalar(text("SELECT version_num FROM public.alembic_version")) == "0006"
        )


async def test_fixed_penalty_back_returns_to_types(factory, settings):
    from app.repositories.base import Repository
    from app.services.penalties import PenaltyService

    async with factory.begin() as session:
        actor = await session.scalar(select(User).where(User.role == "max"))
        await PenaltyService(Repository(session), settings).configure(
            actor,
            None,
            name="Фиксированный",
            emoji="⚠️",
            description="",
            calculation_type="FIXED",
            rate=250,
        )
    driver = BotDriver(factory, settings)
    await driver.text(1001, "/menu")
    await driver.press(1001, "⚠️ Штраф")
    await driver.press(1001, "Макс")
    await driver.press(1001, "▶️")
    await driver.press(1001, "Фиксированный")
    await driver.press(1001, "⬅️ Назад")
    assert "Выбери тип" in driver.screen(1001).text
    await driver.press(1001, "Отмена")
    async with factory() as session:
        assert await session.scalar(select(func.count(Penalty.id))) == 0
    await driver.bot.session.close()
