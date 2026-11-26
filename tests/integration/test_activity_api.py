import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from httpx import ASGITransport, AsyncClient

from app.web.application import create_app

pytestmark = pytest.mark.integration


def headers(settings, telegram_id):
    data = {"auth_date": str(int(time.time())), "user": json.dumps({"id": telegram_id})}
    check = "\n".join(f"{key}={data[key]}" for key in sorted(data))
    secret = hmac.new(
        b"WebAppData", settings.bot_token.get_secret_value().encode(), hashlib.sha256
    ).digest()
    data["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return {"Authorization": f"tma {urlencode(data)}"}


async def test_activity_lifecycle_and_board_privacy(factory, settings):
    app = create_app(settings, session_factory=factory, workers=False)
    me = headers(settings, settings.user_max_id)
    peer = headers(settings, settings.user_vika_id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        tag_response = await client.post(
            "/api/miniapp/tags",
            headers=me,
            json={"name": "Спорт", "color": "#11aa33", "operation_key": "sport-tag"},
        )
        assert tag_response.status_code == 201, tag_response.text
        tag = tag_response.json()
        template_response = await client.post(
            "/api/miniapp/templates",
            headers=me,
            json={
                "title": "Sport",
                "points": 100,
                "pricing_mode": "PER_TIME",
                "base_quantity": 30,
                "operation_key": "activity-template",
                "tag_ids": [tag["id"]],
            },
        )
        assert template_response.status_code == 201, template_response.text
        template = template_response.json()
        task_response = await client.post(
            "/api/miniapp/tasks",
            headers=me,
            json={
                "title": "Sport",
                "target": "SELF",
                "template_id": template["id"],
                "template_version": template["version"],
                "template_quantity": 60,
                "operation_key": "activity-task",
            },
        )
        assert task_response.status_code == 201, task_response.text
        task = task_response.json()
        detail = await client.get(f"/api/miniapp/tasks/{task['id']}", headers=me)
        assert [t["id"] for t in detail.json()["tags"]] == [tag["id"]]
        done = await client.post(
            f"/api/miniapp/tasks/{task['id']}/actions",
            headers=me,
            json={"version": task["version"], "status": "DONE"},
        )
        assert done.status_code == 200, done.text
        board_response = await client.post(
            "/api/miniapp/boards",
            headers=me,
            json={"name": "Год", "operation_key": "activity-board"},
        )
        assert board_response.status_code == 201, board_response.text
        board = board_response.json()
        tracker_response = await client.post(
            f"/api/miniapp/boards/{board['id']}/trackers",
            headers=me,
            json={
                "name": "Спорт",
                "color": "#11aa33",
                "metric": "TIME",
                "unit": None,
                "position": 0,
                "tag_ids": [tag["id"]],
                "operation_key": "activity-tracker",
            },
        )
        assert tracker_response.status_code == 201, tracker_response.text
        tracker = tracker_response.json()
        bootstrap = (await client.get("/api/miniapp/bootstrap", headers=me)).json()
        year = int(done.json()["completed_at"][:4])
        url = f"/api/miniapp/boards/{board['id']}/activity?year={year}&user_id={bootstrap['user']['id']}"
        activity = await client.get(url, headers=me)
        assert activity.status_code == 200, activity.text
        assert sum(activity.json()["trackers"][0]["days"].values()) == 60
        assert (await client.get(url, headers=peer)).status_code == 422
        day = next(iter(activity.json()["trackers"][0]["days"]))
        daily = await client.get(
            f"/api/miniapp/trackers/{tracker['id']}/days/{day}?user_id={bootstrap['user']['id']}",
            headers=me,
        )
        assert daily.status_code == 200 and daily.json()["count"] == 1
        reopened = await client.post(
            f"/api/miniapp/tasks/{task['id']}/actions",
            headers=me,
            json={"version": done.json()["version"], "status": "TODO", "confirmed": True},
        )
        assert reopened.status_code == 200, reopened.text
        assert (await client.get(url, headers=me)).json()["trackers"][0]["days"] == {}
        archived = await client.post(
            f"/api/miniapp/tags/{tag['id']}/archive", headers=me, json={"version": tag["version"]}
        )
        assert archived.status_code == 200, archived.text
        edited_tracker = await client.patch(
            f"/api/miniapp/trackers/{tracker['id']}",
            headers=me,
            json={
                "version": tracker["version"],
                "name": "Sport updated",
                "color": "#11aa33",
                "metric": "TIME",
                "unit": None,
                "position": 0,
                "tag_ids": [tag["id"]],
            },
        )
        assert edited_tracker.status_code == 200, edited_tracker.text
        inherited_archived = await client.post(
            "/api/miniapp/tasks",
            headers=me,
            json={
                "title": "Sport",
                "target": "SELF",
                "template_id": template["id"],
                "template_version": template["version"],
                "template_quantity": 30,
                "operation_key": "archived-template-task",
                "tag_ids": [tag["id"]],
            },
        )
        assert inherited_archived.status_code == 201, inherited_archived.text
        forbidden_archived = await client.post(
            "/api/miniapp/tasks",
            headers=me,
            json={
                "title": "Manual",
                "target": "SELF",
                "operation_key": "archived-manual-task",
                "tag_ids": [tag["id"]],
            },
        )
        assert forbidden_archived.status_code == 422
