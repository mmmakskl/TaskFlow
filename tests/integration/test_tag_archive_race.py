"""An in-flight archive must serialize before a new tag assignment."""

import asyncio

import pytest

from app.models.entities import Tag, User
from app.repositories.activity import ActivityRepository
from app.repositories.tasks import TaskRepository
from app.services.activity import ActivityService
from app.services.rules import DomainError
from app.services.tasks import TaskService

pytestmark = pytest.mark.integration


async def test_archiving_tag_serializes_new_task_assignment(factory, settings):
    async with factory.begin() as session:
        actor = (await TaskRepository(session).users())[0]
        tag = await ActivityService(ActivityRepository(session), settings).save_tag(
            actor, "Race", "#112233", key="race-tag"
        )
        actor_id, tag_id = actor.id, tag.id

    async with factory() as archive_session:
        async with archive_session.begin():
            tag = await archive_session.get(Tag, tag_id, with_for_update=True)
            tag.archived = True
            await archive_session.flush()

            async def assign():
                async with factory.begin() as session:
                    actor = await TaskRepository(session).get(User, actor_id)
                    return await TaskService(TaskRepository(session), settings).create(
                        actor, "New", "race-task", tag_ids=[tag_id]
                    )

            attempt = asyncio.create_task(assign())
            await asyncio.sleep(0.2)
            blocked = not attempt.done()
        try:
            with pytest.raises(DomainError, match="архивирован"):
                await attempt
        finally:
            assert blocked
