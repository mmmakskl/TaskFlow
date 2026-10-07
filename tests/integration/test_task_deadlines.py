from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.bot.callbacks.actions import Action
from app.models import Penalty, ScoreEvent, TaskTarget
from app.repositories.tasks import TaskRepository
from app.services.rules import DomainError, StaleAction
from app.services.tasks import TaskService
from tests.integration.test_web_and_bot import BotDriver

pytestmark = pytest.mark.integration
AT = datetime(2026, 10, 8, 12, tzinfo=UTC)


@pytest.fixture
def clock(monkeypatch):
    for module in (
        "app.services.tasks",
        "app.services.ui",
        "app.repositories.tasks",
        "app.bot.handlers.penalties",
    ):
        monkeypatch.setattr(module + ".utcnow", lambda: AT)


async def task_for(service, owner, peer, title, deadline, *, shared=False, done=False):
    task = await service.create(
        owner,
        title,
        title,
        deadline=deadline,
        estimate=60,
        target=TaskTarget.SHARED if shared else TaskTarget.PEER,
    )
    await service.rate(peer, task.id, 500, task.version)
    if done:
        await service.change_status(peer, task.id, "DONE", task.version)
    return task


async def test_overdue_scope_includes_late_done_excludes_on_time_cancelled_and_future(
    factory, settings, clock
):
    async with factory.begin() as session:
        repo = TaskRepository(session)
        owner, peer = await repo.users()
        service = TaskService(repo, settings)
        late = await task_for(service, owner, peer, "late done", AT - timedelta(hours=1), done=True)
        active = await task_for(service, owner, peer, "late active", AT - timedelta(seconds=1))
        shared = await task_for(
            service, owner, peer, "late shared", AT - timedelta(days=1), shared=True
        )
        await task_for(service, owner, peer, "on time", AT, done=True)
        await task_for(service, owner, peer, "future", AT + timedelta(seconds=1))
        await task_for(service, owner, peer, "no deadline", None)
        cancelled = await task_for(service, owner, peer, "cancelled", AT - timedelta(days=1))
        await service.change_status(peer, cancelled.id, "CANCELLED", cancelled.version, True)
        rows, count = await repo.list(owner, "overdue", scope="peer")
        assert count == 3 and {t.id for t in rows} == {late.id, active.id, shared.id}
        mine, count = await repo.list(owner, "overdue", scope="mine")
        assert count == 1 and mine[0].id == shared.id
        with pytest.raises(DomainError, match="нет просрочки"):
            await service.deadline_penalty_task(owner, cancelled.id, cancelled.version)
        with pytest.raises(StaleAction):
            await service.deadline_penalty_task(owner, active.id, active.version + 1)


@pytest.mark.parametrize("done,shared", [(False, False), (True, False), (True, True)])
async def test_peer_overdue_manual_penalty_prefilled_confirmed_and_idempotent(
    factory, settings, clock, done, shared
):
    async with factory.begin() as session:
        repo = TaskRepository(session)
        owner, peer = await repo.users()
        task = await task_for(
            TaskService(repo, settings),
            owner,
            peer,
            "Диплом <час>",
            AT - timedelta(days=1, hours=2),
            shared=shared,
            done=done,
        )
        task_id, recipient = task.id, peer.id
    driver = BotDriver(factory, settings)
    await driver.text(1001, "/start")
    await driver.press(1001, "Все задачи")
    await driver.press(1001, "Вика")
    await driver.press(1001, "Просроченные")
    row = next(
        b.text
        for row in driver.screen(1001).reply_markup.inline_keyboard
        for b in row
        if "Диплом" in b.text
    )
    assert "🔴" in row and "~1 ч" in row
    await driver.press(1001, "Диплом")
    assert (
        "Выполнено с опозданием" in driver.screen(1001).text
        if done
        else "Просрочено на" in driver.screen(1001).text
    )
    assert "1 д 2 ч" in driver.screen(1001).text
    assert "&lt;час&gt;" in driver.screen(1001).text
    entry = await driver.press(1001, "Назначить штраф")
    if shared:
        await driver.press(1001, "Вика")
    assert "Штраф для: Вика" in driver.screen(1001).text
    await driver.press(1001, "▶️")
    assert "Штраф для: Вика" in driver.screen(1001).text
    await driver.press(1001, "Срыв дедлайна")
    await driver.text(1001, "350")
    assert "Просрочка задачи" in driver.screen(1001).text
    await driver.press(1001, "Оставить комментарий")
    assert "Вика" in driver.screen(1001).text and "350" in driver.screen(1001).text
    async with factory() as session:
        assert await session.scalar(select(func.count(Penalty.id))) == 0
    confirm = await driver.press(1001, "✅ Назначить")
    await driver.press(1001, data=confirm)
    async with factory() as session:
        penalty = await session.scalar(select(Penalty))
        assert penalty.target_user_id == recipient and penalty.points == 350
        assert f"#{task_id}" in penalty.comment and "Диплом <час>" in penalty.comment
        assert "Дедлайн:" in penalty.comment
        assert await session.scalar(select(func.count(Penalty.id))) == 1
        assert (
            await session.scalar(
                select(func.count(ScoreEvent.id)).where(ScoreEvent.category == "penalty")
            )
            == 1
        )
    await driver.press(1001, data=entry)
    assert await session_count(factory) == 1
    await driver.bot.session.close()


async def session_count(factory):
    async with factory() as session:
        return await session.scalar(select(func.count(Penalty.id)))


async def test_forged_future_task_penalty_cannot_open_form(factory, settings, clock):
    async with factory.begin() as session:
        repo = TaskRepository(session)
        owner, peer = await repo.users()
        task = await task_for(
            TaskService(repo, settings), owner, peer, "Future", AT + timedelta(days=1)
        )
        action = Action(name="task_penalty", entity=task.id, version=task.version).pack()
    driver = BotDriver(factory, settings)
    await driver.text(1001, "/start")
    await driver.press(1001, data=action)
    assert await session_count(factory) == 0
    await driver.bot.session.close()
