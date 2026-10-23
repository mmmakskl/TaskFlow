import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from httpx import ASGITransport, AsyncClient

from app.web.application import create_app

pytestmark = pytest.mark.integration


def init_data(settings, telegram_id: int) -> str:
    values = {
        "auth_date": str(int(time.time())),
        "user": json.dumps({"id": telegram_id, "first_name": "Test"}, separators=(",", ":")),
    }
    check = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    token = settings.bot_token.get_secret_value()
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


async def test_miniapp_auth_and_task_workflow(factory, settings) -> None:
    app = create_app(settings, session_factory=factory, workers=False)
    headers_max = {"Authorization": f"tma {init_data(settings, settings.user_max_id)}"}
    headers_vika = {"Authorization": f"tma {init_data(settings, settings.user_vika_id)}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        page = await client.get("/miniapp")
        assert page.status_code == 200 and "telegram-web-app.js" in page.text
        assert (await client.get("/api/miniapp/bootstrap")).status_code == 401
        unauthorized = {"Authorization": f"tma {init_data(settings, 9999)}"}
        assert (await client.get("/api/miniapp/bootstrap", headers=unauthorized)).status_code == 403

        bootstrap = await client.get("/api/miniapp/bootstrap", headers=headers_max)
        assert bootstrap.status_code == 200
        assert bootstrap.json()["user"]["name"] == settings.user_max_name

        created = await client.post(
            "/api/miniapp/tasks",
            headers=headers_max,
            json={
                "title": "Mini App task",
                "target": "SELF",
                "proposed_points": 500,
                "operation_key": "miniapp-task-workflow",
            },
        )
        assert created.status_code == 201, created.text
        task = created.json()
        assert task["status"] == "BACKLOG"

        rated = await client.post(
            f"/api/miniapp/tasks/{task['id']}/actions",
            headers=headers_vika,
            json={"version": task["version"], "status": "RATE", "points": 500},
        )
        assert rated.status_code == 200, rated.text
        started = await client.post(
            f"/api/miniapp/tasks/{task['id']}/actions",
            headers=headers_max,
            json={"version": rated.json()["version"], "status": "IN_PROGRESS"},
        )
        assert started.status_code == 200, started.text
        completed = await client.post(
            f"/api/miniapp/tasks/{task['id']}/actions",
            headers=headers_max,
            json={"version": started.json()["version"], "status": "DONE"},
        )
        assert completed.status_code == 200, completed.text
        result = await client.get("/api/miniapp/results", headers=headers_max)
        max_score = next(
            row for row in result.json()["scores"] if row["name"] == settings.user_max_name
        )
        assert max_score["earned"] == 500 and completed.json()["awarded_points"] == 500
