import asyncio
import os
import sys

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url

from app.models import AuditEvent, Penalty, PenaltyType, ScoreEvent, Task, TaskLink, TaskTarget
from app.repositories.tasks import TaskRepository
from app.services.penalties import PenaltyService
from app.services.tasks import TaskService

pytestmark = pytest.mark.integration


async def alembic(settings, *arguments):
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        *arguments,
        env={**os.environ, "DATABASE_URL": settings.database_url.get_secret_value()},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    return process.returncode, (stdout + stderr).decode()


async def test_reversible_guards_preserve_all_data(factory, settings):
    async with factory.begin() as session:
        repo = TaskRepository(session)
        owner, peer = await repo.users()
        service = TaskService(repo, settings)
        task = await service.create(owner, "Retained", "migration", estimate=30)
        await service.rate(peer, task.id, 500, task.version)
        await service.change_status(owner, task.id, "DONE", task.version)
        parent = await service.create(owner, "Epic", "migration-epic")
        await service.rate(peer, parent.id, 900, parent.version)
        await service.create(
            owner,
            "Subtask",
            "migration-subtask",
            target=TaskTarget.SELF,
            parent_task_id=parent.id,
            parent_version=parent.version,
        )
        await service.create_link(
            owner, parent.id, task.id, "RELATED", parent.version, task.version, "migration-link"
        )
        penalties = PenaltyService(repo, settings)
        kind = await session.scalar(select(PenaltyType).where(PenaltyType.seed_key == "instagram"))
        await penalties.create(
            peer, owner.id, await penalties.preview(kind.id, 5, None), "migration-penalty", True
        )

    async def snapshot():
        async with factory() as session:
            return [
                list((await session.execute(select(model.__table__).order_by(model.id))).mappings())
                for model in (Task, TaskLink, Penalty, ScoreEvent, AuditEvent)
            ]

    before = await snapshot()
    code, output = await alembic(settings, "downgrade", "0004")
    assert code != 0 and ("no destructive downgrade" in output or "Task links exist" in output)
    assert await snapshot() == before
    code, output = await alembic(settings, "upgrade", "head")
    assert code == 0, output
    assert await snapshot() == before
    async with factory() as session:
        assert (
            await session.scalar(text("SELECT version_num FROM public.alembic_version")) == "0008"
        )
    code, output = await alembic(settings, "check")
    assert code == 0, output


async def test_destructive_downgrades_refused_without_data_loss(factory, settings):
    code, output = await alembic(settings, "downgrade", "0001")
    assert code != 0 and "RuntimeError" in output
    async with factory() as session:
        assert (
            await session.scalar(text("SELECT version_num FROM public.alembic_version")) == "0008"
        )
        assert await session.scalar(select(func.count(PenaltyType.id))) == 7
    assert (await alembic(settings, "upgrade", "head"))[0] == 0


async def test_concurrent_empty_database_migrations(factory, settings):
    async with factory.begin() as session:
        await session.execute(text("DROP SCHEMA taskflow CASCADE"))
        await session.execute(text("DROP TABLE public.alembic_version"))
    runs = await asyncio.gather(
        alembic(settings, "upgrade", "head"), alembic(settings, "upgrade", "head")
    )
    for code, output in runs:
        assert code == 0, output
    async with factory() as session:
        assert (
            await session.scalar(text("SELECT version_num FROM public.alembic_version")) == "0008"
        )
        assert await session.scalar(select(func.count(Task.id))) == 0
        assert set(
            await session.scalars(
                text("SELECT tablename FROM pg_tables WHERE schemaname='taskflow'")
            )
        ) >= {"users", "tasks", "task_links", "penalty_types", "penalties", "audit_events"}


async def test_invalid_legacy_data_aborts_guards_without_rewriting_rows(factory, settings):
    async with factory.begin() as session:
        await session.execute(text("DROP SCHEMA taskflow CASCADE"))
        await session.execute(text("DROP TABLE public.alembic_version"))
    assert (await alembic(settings, "upgrade", "0002"))[0] == 0
    async with factory.begin() as session:
        await session.execute(
            text(
                "INSERT INTO taskflow.users (id,telegram_id,display_name,role,timezone,weekly_target,created_at,updated_at) VALUES (1,1001,'Max','max','UTC',10000,now(),now()),(2,1002,'Vika','vika','UTC',10000,now(),now())"
            )
        )
        await session.execute(
            text(
                "INSERT INTO taskflow.tasks (owner_user_id,evaluator_user_id,title,status,operation_key,version,created_at,updated_at) VALUES (1,2,'Invalid legacy row','TODO','legacy-invalid',1,now(),now())"
            )
        )
    code, output = await alembic(settings, "upgrade", "head")
    assert code != 0 and "ck_tasks_active_rated" in output
    async with factory() as session:
        assert (
            await session.scalar(text("SELECT version_num FROM public.alembic_version")) == "0002"
        )
        task = (await session.execute(text("SELECT status,rated_points FROM taskflow.tasks"))).one()
        assert task.status == "TODO" and task.rated_points is None
        assert not await session.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='ck_tasks_version' AND conrelid='taskflow.tasks'::regclass)"
            )
        )


async def test_initial_revision_refuses_destructive_downgrade(factory, settings):
    async with factory.begin() as session:
        await session.execute(text("DROP SCHEMA taskflow CASCADE"))
        await session.execute(text("DROP TABLE public.alembic_version"))
    code, output = await alembic(settings, "upgrade", "0001")
    assert code == 0, output
    code, output = await alembic(settings, "downgrade", "base")
    assert code != 0 and "RuntimeError" in output
    async with factory() as session:
        assert (
            await session.scalar(text("SELECT version_num FROM public.alembic_version")) == "0001"
        )
        assert await session.scalar(text("SELECT to_regclass('taskflow.tasks')")) is not None
    code, output = await alembic(settings, "upgrade", "head")
    assert code == 0, output


async def test_migration_reflection_with_application_schema_first(factory, settings):
    # PostgreSQL's default "$user", public search_path exposes this with the
    # local Compose role named taskflow. Reproduce it for any disposable test role.
    name = make_url(settings.database_url.get_secret_value()).database
    assert name and name.endswith("_test")
    async with factory() as session:
        bind = session.get_bind()
        quoted = bind.dialect.identifier_preparer.quote(name)
    async with factory.begin() as session:
        await session.execute(text(f"ALTER DATABASE {quoted} SET search_path TO taskflow, public"))
    try:
        code, output = await alembic(settings, "check")
        assert code == 0, output
    finally:
        async with factory.begin() as session:
            await session.execute(text(f"ALTER DATABASE {quoted} RESET search_path"))
