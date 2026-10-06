import asyncio
import os
import sys
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings
from app.db.seed import seed
from app.db.session import database


@pytest.fixture
def settings() -> Settings:
    return Settings(
        bot_token="123456789:TEST_ONLY_TOKEN",
        webhook_secret="test-secret-" + "x" * 32,
        database_url=os.environ.get(
            "TEST_DATABASE_URL", "postgresql://postgres@localhost/taskflow_test"
        ),
        user_max_id=1001,
        user_vika_id=1002,
        register_webhook=False,
        environment="test",
        rating_notification_debounce_seconds=1,
        _env_file=None,
    )


@pytest.fixture
async def factory(settings: Settings) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is required for real PostgreSQL integration")
    if not (make_url(url).database or "").endswith("_test"):
        pytest.fail("Integration database name MUST end in _test; never use production")
    engine, sessions = database(settings)
    async with engine.begin() as connection:
        await connection.execute(text("DROP SCHEMA IF EXISTS taskflow CASCADE"))
        await connection.execute(text("DROP TABLE IF EXISTS public.alembic_version"))
    environment = {**os.environ, "DATABASE_URL": url}
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.db.migrate",
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    assert process.returncode == 0, (stdout + stderr).decode()
    async with sessions.begin() as session:
        await seed(session, settings)
    yield sessions
    await engine.dispose()
