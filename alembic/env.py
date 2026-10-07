import asyncio
import os

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from alembic import context
from app.models import Base

load_dotenv()


def include_name(name: str | None, type_: str, parent_names: dict[str, str | None]) -> bool:
    if type_ == "schema":
        return name == "taskflow"
    return parent_names.get("schema_name") == "taskflow"


def migrate(connection: object) -> None:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        include_schemas=True,
        include_name=include_name,
    )
    with context.begin_transaction():
        context.run_migrations()


async def online() -> None:
    url = make_url(os.environ["DATABASE_URL"])
    query = dict(url.query)
    if "sslmode" in query:
        query["ssl"] = query.pop("sslmode")
    engine = create_async_engine(
        url.set(drivername="postgresql+asyncpg", query=query),
        poolclass=NullPool,
        hide_parameters=True,
        connect_args={
            "timeout": 15,
            "command_timeout": 60,
            "statement_cache_size": 0,
            # Reflect taskflow explicitly even when a same-named login makes
            # PostgreSQL's default "$user" search path resolve to that schema.
            "server_settings": {"search_path": "public"},
        },
    )
    async with engine.connect() as connection:
        # Session advisory lock remains held through Alembic's transaction commits.
        await connection.execute(text("SELECT pg_advisory_lock(78421309)"))
        await connection.commit()
        try:
            await connection.run_sync(migrate)
        finally:
            await connection.execute(text("SELECT pg_advisory_unlock(78421309)"))
            await connection.commit()
    await engine.dispose()


if context.is_offline_mode():
    context.configure(
        url="postgresql://", target_metadata=Base.metadata, literal_binds=True, include_schemas=True
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(online())
