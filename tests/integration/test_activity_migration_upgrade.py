"""Upgrade populated 0008 and verify the non-destructive downgrade policy."""

import pytest
from sqlalchemy import text

from app.repositories.tasks import TaskRepository
from app.services.tasks import TaskService
from tests.integration.test_business_migrations import alembic

pytestmark = pytest.mark.integration


async def test_populated_0008_upgrade_preserves_data_and_refuses_lossy_downgrade(factory, settings):
    code, output = await alembic(settings, "downgrade", "0008")
    assert code == 0, output
    async with factory.begin() as session:
        repo = TaskRepository(session)
        owner, peer = await repo.users()
        tasks = TaskService(repo, settings)
        task = await tasks.create(owner, "Existing task", "legacy-task")
        await tasks.rate(peer, task.id, 500, task.version)
        await tasks.change_status(owner, task.id, "DONE", task.version)

    async def snapshot():
        async with factory() as session:
            return [
                (await session.execute(text(f"SELECT * FROM taskflow.{table} ORDER BY id")))
                .mappings()
                .all()
                for table in ("tasks", "task_templates", "score_events")
            ]

    before = await snapshot()
    assert all(before)
    code, output = await alembic(settings, "upgrade", "0009")
    assert code == 0, output
    assert await snapshot() == before
    async with factory.begin() as session:
        assert (
            await session.scalar(text("SELECT version_num FROM public.alembic_version")) == "0009"
        )
        await session.execute(
            text(
                "INSERT INTO taskflow.tags (name, normalized_name, operation_key, color, created_at, updated_at) VALUES ('Existing tag', 'existing tag', 'migration-tag', '#112233', now(), now())"
            )
        )
    code, output = await alembic(settings, "downgrade", "0008")
    assert code != 0 and "Activity configuration exists" in output
    assert await snapshot() == before
    async with factory() as session:
        assert (
            await session.scalar(text("SELECT version_num FROM public.alembic_version")) == "0009"
        )
        assert await session.scalar(text("SELECT count(*) FROM taskflow.tags")) == 1
