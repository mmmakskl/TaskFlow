from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config.settings import Settings


def database(settings: Settings) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    # Bound connection usage across the worker and HTTP readiness checks.
    engine = create_async_engine(
        settings.async_database_url,
        hide_parameters=True,
        pool_size=3,
        max_overflow=2,
        pool_pre_ping=True,
        pool_timeout=5,
        connect_args={"timeout": 10, "command_timeout": 30, "statement_cache_size": 0},
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False)
