"""Exercise actual aiogram routing with durable PostgreSQL dialogs and services."""

import pytest
from sqlalchemy import func, select

from app.models import ScoreEvent, Task, TaskTemplate, User
from app.repositories.templates import TaskTemplateRepository
from app.services.templates import TaskTemplateService
from tests.integration.test_form_navigation import data_for
from tests.integration.test_web_and_bot import BotDriver

pytestmark = pytest.mark.integration


async def draft(driver, target="Себе", title="Offer"):
    await driver.text(1001, "/menu")
    await driver.press(1001, "➕ Задача")
    await driver.press(1001, target)
    await driver.text(1001, title)


@pytest.mark.parametrize(
    "target,owner,kind",
    [("Себе", 1001, "PERSONAL"), ("Другому", 1002, "PERSONAL"), ("Вместе", 1001, "SHARED")],
)
async def test_explicit_draft_suggested_accept_shared_completion(
    factory, settings, target, owner, kind
):
    driver = BotDriver(factory, settings)
    await draft(driver, target, "<Offer>")
    await driver.press(1001, "Баллы")
    await driver.text(1001, "725")
    await driver.press(1001, "⏱ Время")
    stale = data_for(driver, 1001, "30 мин")
    await driver.press(1001, "⬅️ Назад")
    await driver.press(1001, data=stale)
    await driver.press(1001, "Комментарий")
    await driver.text(1001, "<Comment>")
    async with factory() as session:
        assert await session.scalar(select(func.count(Task.id))) == 0
    create = await driver.press(1001, "✅ Создать")
    await driver.press(1001, data=create)
    await driver.text(1002, "/menu")
    await driver.press(1002, "Входящие")
    assert "&lt;Offer&gt;" in driver.screen(1002).text
    await driver.press(1002, "Принять 725")
    await driver.text(owner, "/menu")
    await driver.press(owner, "Все задачи")
    await driver.press(owner, "Offer")
    await driver.press(owner, "Выполнено")
    async with factory() as session:
        task = await session.scalar(select(Task))
        assert task.status == "DONE" and task.kind == kind and task.awarded_points == 725
        assert await session.scalar(select(func.count(Task.id))) == 1
        assert await session.scalar(select(func.count(ScoreEvent.id))) == (
            2 if kind == "SHARED" else 1
        )
    await driver.bot.session.close()


async def test_peer_readonly_reject_nonce_and_manual_rerate(factory, settings):
    driver = BotDriver(factory, settings)
    await draft(driver)
    await driver.press(1001, "✅ Создать")
    await driver.text(1002, "/menu")
    await driver.press(1002, "Входящие")
    await driver.press(1002, "Отклонить")
    stale = data_for(driver, 1002, "✅ Отклонить")
    await driver.press(1002, "Входящие")
    await driver.press(1002, data=stale)
    async with factory() as session:
        assert (await session.scalar(select(Task))).status == "BACKLOG"
    await driver.press(1002, "500")
    await driver.press(1002, "Меню")
    await driver.press(1002, "Все задачи")
    await driver.press(1002, "Макс")
    await driver.press(1002, "Offer")
    assert not any(
        b.text == "✏️ Изменить"
        for row in driver.screen(1002).reply_markup.inline_keyboard
        for b in row
    )
    await driver.press(1002, "Изменить цену")
    await driver.text(1002, "800")
    assert "500 → 800" in driver.screen(1002).text
    old = data_for(driver, 1002, "✅ Подтвердить")
    await driver.press(1002, "Отмена")
    await driver.press(1002, data=old)
    async with factory() as session:
        assert (await session.scalar(select(Task))).rated_points == 500
    await driver.press(1002, "Изменить цену")
    await driver.text(1002, "900")
    await driver.press(1002, "Подтвердить")
    async with factory() as session:
        assert (await session.scalar(select(Task))).rated_points == 900
    await driver.text(1001, "/menu")
    await driver.press(1001, "➕ Задача")
    await driver.press(1001, "Другому")
    await driver.text(1001, "Reject this")
    await driver.press(1001, "✅ Создать")
    await driver.text(1002, "/menu")
    await driver.press(1002, "Входящие")
    await driver.press(1002, "Отклонить")
    old = await driver.press(1002, "✅ Отклонить")
    await driver.press(1002, data=old)
    async with factory() as session:
        assert (
            await session.scalar(select(Task).where(Task.title == "Reject this"))
        ).status == "CANCELLED"
    await driver.bot.session.close()


async def test_template_crud_save_draft_required_points_and_use(factory, settings):
    driver = BotDriver(factory, settings)
    await draft(driver, title="Saved template")
    await driver.press(1001, "Сохранить как шаблон")
    await driver.text(1001, "0")
    await driver.text(1001, "450")
    assert "Шаблон сохранён" in driver.screen(1001).text
    async with factory() as session:
        assert await session.scalar(select(func.count(Task.id))) == 0
        assert await session.scalar(
            select(TaskTemplate).where(TaskTemplate.title == "Saved template")
        )
    await driver.press(1001, "Отмена")
    await driver.press(1001, "Шаблоны")
    await driver.press(1001, "Новый шаблон")
    await driver.text(1001, "Custom template")
    await driver.text(1001, "600")
    await driver.text(1001, "45")
    await driver.press(1001, "Баллы")
    await driver.text(1001, "650")
    await driver.press(1001, "Название")
    await driver.text(1001, "Changed template")
    await driver.press(1001, "Время")
    await driver.press(1001, "Не указывать")
    await driver.press(1001, "Выключить")
    nonce = data_for(driver, 1001, "✅ Подтвердить")
    await driver.press(1001, "Отмена")
    await driver.press(1001, data=nonce)
    await driver.press(1001, "Выключить")
    await driver.press(1001, "Подтвердить")
    assert "Выключен" in driver.screen(1001).text
    await driver.press(1001, "Включить")
    await driver.press(1001, "Создать задачу")
    await driver.press(1001, "Для кого")
    await driver.press(1001, "Вместе")
    await driver.press(1001, "✅ Создать")
    assert "К выполнению" in driver.screen(1001).text
    async with factory() as session:
        task = await session.scalar(select(Task))
        assert task.status == "TODO" and task.kind == "SHARED" and task.rated_points == 650
    await driver.bot.session.close()


@pytest.mark.parametrize("disable", [False, True])
async def test_stale_template_refresh_retains_manual_changes_and_explicit_resubmit(
    factory, settings, disable
):
    driver = BotDriver(factory, settings)
    await driver.text(1001, "/menu")
    await driver.press(1001, "Шаблоны")
    await driver.press(1001, "Английский")
    await driver.press(1001, "Создать задачу")
    await driver.press(1001, "Название")
    await driver.text(1001, "Manual override")
    async with factory.begin() as session:
        t = await session.scalar(select(TaskTemplate).where(TaskTemplate.seed_key == "english"))
        actor = await session.scalar(select(User).where(User.telegram_id == 1002))
        await TaskTemplateService(TaskTemplateRepository(session), settings).edit(
            actor, t.id, t.version, points=800, is_active=not disable
        )
    await driver.press(1001, "✅ Создать")
    assert "Manual override" in driver.screen(1001).text and "800" in driver.screen(1001).text
    async with factory() as session:
        assert await session.scalar(select(func.count(Task.id))) == 0
    await driver.press(1001, "✅ Создать")
    async with factory() as session:
        task = await session.scalar(select(Task))
        assert (
            task.title == "Manual override"
            and task.status == "BACKLOG"
            and task.proposed_points == 800
        )
        assert (task.template_id is None) == disable
    await driver.bot.session.close()


async def test_template_pages_snapshot_and_peer_requires_acceptance(factory, settings):
    driver = BotDriver(factory, settings)
    async with factory.begin() as session:
        actor = await session.scalar(select(User).where(User.telegram_id == 1001))
        service = TaskTemplateService(TaskTemplateRepository(session), settings)
        for i in range(6):
            await service.create(actor, f"Extra {i}", 400, f"extra-{i}", 30)
    await driver.text(1001, "/menu")
    await driver.press(1001, "Шаблоны")
    buttons = [b for row in driver.screen(1001).reply_markup.inline_keyboard for b in row]
    assert len([b for b in buttons if b.callback_data.startswith("a:tpl_view:")]) == 5
    await driver.press(1001, "▶️")
    assert "Extra 5" in str(driver.screen(1001).reply_markup)
    await driver.press(1001, "Extra 5")
    await driver.press(1001, "Создать задачу")
    await driver.press(1001, "Для кого")
    await driver.press(1001, "Другому")
    await driver.press(1001, "✅ Создать")
    async with factory.begin() as session:
        task = await session.scalar(select(Task))
        assert task.status == "BACKLOG" and task.proposed_points == 400
        original = task.template_snapshot.copy()
        actor = await session.scalar(select(User).where(User.telegram_id == 1001))
        t = await session.get(TaskTemplate, task.template_id)
        await TaskTemplateService(TaskTemplateRepository(session), settings).edit(
            actor, t.id, t.version, points=900
        )
        assert task.template_snapshot == original
    await driver.text(1002, "/menu")
    await driver.press(1002, "Входящие")
    await driver.press(1002, "Принять 400")
    await driver.bot.session.close()


async def test_scopes_whole_user_summary_shared_add_and_semantic_edit_reapproval(factory, settings):
    from app.models import TaskTarget
    from app.repositories.tasks import TaskRepository
    from app.services.tasks import TaskService

    async with factory.begin() as session:
        max_user, vika = list(await session.scalars(select(User).order_by(User.id)))
        service = TaskService(TaskRepository(session), settings)
        for i in range(7):
            t = await service.create(
                max_user,
                f"Plan {i}",
                f"plan-{i}",
                target=TaskTarget.SHARED if i == 0 else TaskTarget.SELF,
            )
            await service.rate(vika, t.id, 100, t.version)
    driver = BotDriver(factory, settings)
    await driver.text(1001, "/menu")
    await driver.press(1001, "Все задачи")
    assert "План 700 баллов" in driver.screen(1001).text
    await driver.press(1001, "▶️")
    assert "План 700 баллов" in driver.screen(1001).text
    await driver.press(1001, "Выполненные")
    assert "План 700 баллов" in driver.screen(1001).text
    await driver.press(1001, "Вика")
    assert "План 100 баллов" in driver.screen(1001).text
    await driver.press(1001, "Общие")
    await driver.press(1001, "Все")
    assert "каждому" in str(driver.screen(1001).reply_markup)
    await driver.press(1001, "Plan 0")
    await driver.press(1001, "Начать")
    await driver.text(1002, "/menu")
    await driver.press(1002, "Все задачи")
    await driver.press(1002, "Plan 0")
    await driver.press(1002, "Изменить цену")
    await driver.text(1002, "250")
    await driver.press(1002, "Подтвердить")
    assert "В работе" in driver.screen(1002).text
    await driver.press(1002, "Изменить")
    await driver.press(1002, "Название")
    await driver.text(1002, "Shared edited")
    assert "Ждёт оценки" in driver.screen(1002).text
    await driver.text(1001, "/menu")
    await driver.press(1001, "Входящие")
    await driver.press(1001, "Принять 250")
    await driver.press(1001, "Меню")
    await driver.press(1001, "Общие задачи")
    await driver.press(1001, "Добавить")
    assert "Напиши название" in driver.screen(1001).text
    await driver.text(1001, "Another shared")
    await driver.press(1001, "✅ Создать")
    async with factory() as session:
        assert (
            await session.scalar(select(Task).where(Task.title == "Another shared"))
        ).kind == "SHARED"
    await driver.bot.session.close()


async def test_template_page_context_survives_edit_disable_cancel_and_create(factory, settings):
    driver = BotDriver(factory, settings)
    async with factory.begin() as session:
        actor = await session.scalar(select(User).where(User.telegram_id == 1001))
        service = TaskTemplateService(TaskTemplateRepository(session), settings)
        for i in range(6):
            await service.create(actor, f"Paged {i}", 400, f"paged-{i}")
    await driver.text(1001, "/menu")
    await driver.press(1001, "Шаблоны")
    await driver.press(1001, "▶️")
    await driver.press(1001, "Paged 5")
    await driver.press(1001, "Баллы")
    await driver.text(1001, "700")
    await driver.press(1001, "Выключить")
    await driver.press(1001, "Отмена")
    await driver.press(1001, "Выключить")
    await driver.press(1001, "Подтвердить")
    await driver.press(1001, "⬅️ Шаблоны")
    assert "Paged 5" in str(driver.screen(1001).reply_markup)
    assert "• 2 •" in str(driver.screen(1001).reply_markup)
    await driver.press(1001, "Paged 5")
    await driver.press(1001, "Название")
    await driver.press(1001, "⬅️ Назад")
    await driver.press(1001, "⬅️ Шаблоны")
    assert "• 2 •" in str(driver.screen(1001).reply_markup)
    await driver.press(1001, "Новый шаблон")
    await driver.text(1001, "Created from second page")
    await driver.text(1001, "500")
    await driver.press(1001, "Не указывать")
    await driver.press(1001, "⬅️ Шаблоны")
    assert "• 2 •" in str(driver.screen(1001).reply_markup)
    assert "Created from second page" in str(driver.screen(1001).reply_markup)
    await driver.bot.session.close()


@pytest.mark.parametrize(
    "current,fields",
    [
        ("TaskForm:draft", {"title": "Draft", "template_id": 1, "template_version": 1}),
        (
            "TaskForm:draft",
            {"title": "Draft", "template_id": 1, "template_version": 1, "template_snapshot": []},
        ),
        ("TaskForm:points", {"title": "Draft", "save_template": True}),
        ("RatingForm:points", {"task_id": 1, "task_version": 1, "rerate": True}),
        (
            "RatingForm:points",
            {"task_id": 1, "task_version": 1, "rerate": True, "old_points": "500"},
        ),
        ("TemplateForm:field", {"field": ["title"]}),
        ("TemplateForm:field", {"field": "estimated_minutes", "title": "Template", "points": True}),
        (
            "ConfirmationForm:action",
            {"confirmation": {"name": "rerate_confirm", "entity": 1, "version": 1}},
        ),
    ],
)
async def test_malformed_new_persisted_forms_restore_dashboard_without_side_effects(
    factory, settings, current, fields
):
    from app.models import Notification
    from app.models.entities import Dialog

    driver = BotDriver(factory, settings)
    await driver.text(1001, "/menu")
    async with factory.begin() as session:
        dialog = await session.scalar(select(Dialog))
        dialog.state = current
        dialog.data = {**dialog.data, "flow": 1, "key": "corrupt-form", **fields}
        template_count = await session.scalar(select(func.count(TaskTemplate.id)))
    await driver.text(1001, "500")
    assert "Черновик сброшен" in driver.screen(1001).text
    assert "НЕДЕЛЯ" in driver.screen(1001).text
    async with factory() as session:
        assert await session.scalar(select(func.count(Task.id))) == 0
        assert await session.scalar(select(func.count(ScoreEvent.id))) == 0
        assert await session.scalar(select(func.count(TaskTemplate.id))) == template_count
        assert await session.scalar(select(func.count(Notification.id))) == 0
        dialog = await session.scalar(select(Dialog))
        assert dialog.state is None and "key" not in dialog.data
    await driver.bot.session.close()
