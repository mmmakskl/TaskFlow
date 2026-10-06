from types import SimpleNamespace

import pytest

from app.models import PenaltyType, User
from app.services.penalties import PenaltyService
from app.services.results import ResultService
from app.services.settings import SettingsService
from app.services.tasks import TaskService
from tests.business_fakes import (
    NOW,
    MemoryPenalties,
    MemoryRepository,
    MemoryResults,
    MemorySession,
    MemoryTasks,
    MemoryUsers,
)


@pytest.fixture
async def business(monkeypatch, settings):
    monkeypatch.setattr("app.services.base.UserRepository", MemoryUsers)
    monkeypatch.setattr("app.services.tasks.utcnow", lambda: NOW)
    monkeypatch.setattr("app.services.penalties.utcnow", lambda: NOW)
    monkeypatch.setattr("app.services.results.utcnow", lambda: NOW)
    session = MemorySession()
    max_user = User(
        id=1,
        telegram_id=settings.user_max_id,
        role="max",
        display_name="Макс",
        timezone=settings.default_timezone,
        weekly_target=10000,
    )
    vika = User(
        id=2,
        telegram_id=settings.user_vika_id,
        role="vika",
        display_name="Вика",
        timezone=settings.default_timezone,
        weekly_target=10000,
    )
    kind = PenaltyType(
        id=1,
        name="Instagram",
        emoji="📱",
        description="Описание",
        calculation_type="PER_MINUTE",
        rate=100,
        version=1,
        sort_order=0,
        is_active=True,
    )
    for row in (max_user, vika, kind):
        session.add(row)
    await session.flush()
    return SimpleNamespace(
        session=session,
        max=max_user,
        vika=vika,
        kind=kind,
        settings=settings,
        tasks=TaskService(MemoryTasks(session), settings),
        penalties=PenaltyService(MemoryPenalties(session), settings),
        results=ResultService(MemoryResults(session), settings),
        config=SettingsService(MemoryRepository(session), settings),
    )
