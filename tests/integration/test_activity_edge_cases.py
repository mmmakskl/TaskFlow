"""End-to-end activity cases for shared ownership, tags and local dates."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from httpx import ASGITransport, AsyncClient

from app.models import Task
from app.web.application import create_app
from tests.integration.test_activity_api import headers

pytestmark = pytest.mark.integration


async def test_shared_task_with_two_matching_tags_is_counted_once_per_person_at_local_date(
    factory, settings
):
    app = create_app(settings, session_factory=factory, workers=False)
    me = headers(settings, settings.user_max_id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:

        async def post(path, payload):
            result = await client.post(path, headers=me, json=payload)
            assert result.status_code in (200, 201), result.text
            return result.json()

        tags = [
            await post(
                "/api/miniapp/tags", {"name": name, "color": "#112233", "operation_key": key}
            )
            for name, key in [("Alpha", "alpha"), ("Beta", "beta")]
        ]
        ids = [t["id"] for t in tags]
        template = await post(
            "/api/miniapp/templates",
            {
                "title": "Both",
                "points": 100,
                "pricing_mode": "FIXED",
                "operation_key": "shared-template",
                "tag_ids": ids,
            },
        )
        task = await post(
            "/api/miniapp/tasks",
            {
                "title": "Both",
                "target": "SHARED",
                "template_id": template["id"],
                "template_version": template["version"],
                "operation_key": "shared-task",
            },
        )
        task = (await client.get(f"/api/miniapp/tasks/{task['id']}", headers=me)).json()
        done = await post(
            f"/api/miniapp/tasks/{task['id']}/actions",
            {"version": task["version"], "status": "DONE"},
        )
        # On the UTC side of a local year boundary, 2023-12-31 21:30 UTC is
        # 2024-01-01 00:30 in Moscow. Use the configured zone to choose the instant.
        local = datetime(2024, 1, 1, 0, 30, tzinfo=ZoneInfo(settings.default_timezone))
        async with factory.begin() as session:
            persisted = await session.get(Task, task["id"])
            persisted.completed_at = local.astimezone(UTC)
        board = await post("/api/miniapp/boards", {"name": "Board", "operation_key": "board-edge"})
        tracker = await post(
            f"/api/miniapp/boards/{board['id']}/trackers",
            {
                "name": "Either",
                "color": "#112233",
                "metric": "TASKS",
                "unit": None,
                "position": 0,
                "tag_ids": ids,
                "operation_key": "tracker-edge",
            },
        )
        boot = (await client.get("/api/miniapp/bootstrap", headers=me)).json()
        for person in (boot["user"], boot["peer"]):
            url = f"/api/miniapp/boards/{board['id']}/activity?year=2024&user_id={person['id']}"
            result = await client.get(url, headers=me)
            assert result.status_code == 200, result.text
            assert result.json()["trackers"][0]["days"] == {"2024-01-01": 1}
            day = await client.get(
                f"/api/miniapp/trackers/{tracker['id']}/days/2024-01-01?user_id={person['id']}",
                headers=me,
            )
            assert day.status_code == 200 and day.json()["count"] == 1
            assert day.json()["points"] == done["awarded_points"]
