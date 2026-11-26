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


async def test_miniapp_searches_all_task_titles(factory, settings) -> None:
    app = create_app(settings, session_factory=factory, workers=False)
    headers = {"Authorization": f"tma {init_data(settings, settings.user_max_id)}"}
    peer_headers = {"Authorization": f"tma {init_data(settings, settings.user_vika_id)}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/miniapp/tasks/search?q=пи")).status_code == 401
        outsider = {"Authorization": f"tma {init_data(settings, 9999)}"}
        assert (
            await client.get("/api/miniapp/tasks/search?q=пи", headers=outsider)
        ).status_code == 403
        titles = ("Написать диплом", "Пойти писать проект", "Пить пиво", "100% план", "План_1")
        created = []
        for index, title in enumerate(titles):
            response = await client.post(
                "/api/miniapp/tasks",
                headers=headers,
                json={"title": title, "target": "SELF", "operation_key": f"search-{index}"},
            )
            assert response.status_code == 201, response.text
            created.append(response.json())

        cancelled = await client.post(
            f"/api/miniapp/tasks/{created[0]['id']}/actions",
            headers=headers,
            json={"version": created[0]["version"], "status": "CANCELLED", "confirmed": True},
        )
        assert cancelled.status_code == 200, cancelled.text
        found = await client.get("/api/miniapp/tasks/search?q=ПИ", headers=headers)
        assert found.status_code == 200, found.text
        assert [item["title"] for item in found.json()["items"]] == [
            "Пить пиво",
            "Пойти писать проект",
            "Написать диплом",
        ]
        assert found.json()["count"] == 3
        assert found.json()["items"][-1]["status"] == "CANCELLED"
        assert (await client.get("/api/miniapp/tasks/search?q=%25", headers=headers)).json()[
            "count"
        ] == 1
        assert (await client.get("/api/miniapp/tasks/search?q=_", headers=headers)).json()[
            "count"
        ] == 1
        peer_found = await client.get("/api/miniapp/tasks/search?q=пи", headers=peer_headers)
        assert peer_found.status_code == 200 and peer_found.json()["count"] == 3

        for index in range(3):
            response = await client.post(
                "/api/miniapp/tasks",
                headers=headers,
                json={
                    "title": f"Пи тест {index}",
                    "target": "SELF",
                    "operation_key": f"search-page-{index}",
                },
            )
            assert response.status_code == 201, response.text
        first_page = (await client.get("/api/miniapp/tasks/search?q=пи", headers=headers)).json()
        second_page = (
            await client.get("/api/miniapp/tasks/search?q=пи&page=1", headers=headers)
        ).json()
        assert first_page["count"] == 6 and len(first_page["items"]) == 5
        assert len(second_page["items"]) == 1

        epic = await client.post(
            "/api/miniapp/tasks",
            headers=headers,
            json={"title": "Родитель", "target": "SELF", "operation_key": "search-epic"},
        )
        assert epic.status_code == 201, epic.text
        approved = await client.post(
            f"/api/miniapp/tasks/{epic.json()['id']}/actions",
            headers=peer_headers,
            json={"version": epic.json()["version"], "status": "RATE", "points": 100},
        )
        assert approved.status_code == 200, approved.text
        child = await client.post(
            "/api/miniapp/tasks",
            headers=headers,
            json={
                "title": "Особая подзадача",
                "target": "SELF",
                "operation_key": "search-child",
                "parent_task_id": epic.json()["id"],
                "parent_version": approved.json()["version"],
            },
        )
        assert child.status_code == 201, child.text
        child_result = (
            await client.get("/api/miniapp/tasks/search?q=особая", headers=headers)
        ).json()
        assert child_result["count"] == 1
        assert child_result["items"][0]["parent_task_id"] == epic.json()["id"]

        # The task-list search keeps the global search's terminal tasks and subtasks.
        global_cancelled = await client.get(
            "/api/miniapp/tasks",
            params={"scope": "all", "filter": "all", "search": "диплом"},
            headers=peer_headers,
        )
        assert global_cancelled.status_code == 200
        assert global_cancelled.json()["items"][0]["status"] == "CANCELLED"
        global_child = await client.get(
            "/api/miniapp/tasks",
            params={"scope": "all", "filter": "all", "search": "особая"},
            headers=peer_headers,
        )
        assert global_child.status_code == 200
        assert global_child.json()["items"][0]["id"] == child.json()["id"]
        filtered = await client.get(
            "/api/miniapp/tasks",
            params={"scope": "all", "filter": "done", "search": "особая"},
            headers=peer_headers,
        )
        assert filtered.status_code == 200 and filtered.json()["count"] == 0


async def test_miniapp_auth_and_task_workflow(factory, settings) -> None:
    app = create_app(settings, session_factory=factory, workers=False)
    headers_max = {"Authorization": f"tma {init_data(settings, settings.user_max_id)}"}
    headers_vika = {"Authorization": f"tma {init_data(settings, settings.user_vika_id)}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        page = await client.get("/miniapp")
        assert page.status_code == 200 and "telegram-web-app.js" in page.text
        assert page.headers["cache-control"] == "no-store, max-age=0"
        for asset in ("miniapp.css", "activity.js", "miniapp.js"):
            assert f"/static/{asset}?v=" in page.text
            assert f'/static/{asset}"' not in page.text
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
        all_tasks = await client.get(
            "/api/miniapp/tasks?scope=mine&filter=all", headers=headers_max
        )
        done_tasks = await client.get(
            "/api/miniapp/tasks?scope=mine&filter=done", headers=headers_max
        )
        completed_id = task["id"]
        assert all(item["id"] != completed_id for item in all_tasks.json()["items"])
        assert any(item["id"] == completed_id for item in done_tasks.json()["items"])
        result = await client.get("/api/miniapp/results", headers=headers_max)
        max_score = next(
            row for row in result.json()["scores"] if row["name"] == settings.user_max_name
        )
        assert max_score["earned"] == 500 and completed.json()["awarded_points"] == 500


async def test_miniapp_epics_subtasks_links_and_progress(factory, settings) -> None:
    app = create_app(settings, session_factory=factory, workers=False)
    headers_max = {"Authorization": f"tma {init_data(settings, settings.user_max_id)}"}
    headers_vika = {"Authorization": f"tma {init_data(settings, settings.user_vika_id)}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        epic_response = await client.post(
            "/api/miniapp/tasks",
            headers=headers_max,
            json={"title": "API epic", "target": "SELF", "operation_key": "api-epic"},
        )
        assert epic_response.status_code == 201, epic_response.text
        epic = epic_response.json()
        approved = await client.post(
            f"/api/miniapp/tasks/{epic['id']}/actions",
            headers=headers_vika,
            json={"version": epic["version"], "status": "RATE", "points": 900},
        )
        assert approved.status_code == 200, approved.text

        child_response = await client.post(
            "/api/miniapp/tasks",
            headers=headers_max,
            json={
                "title": "API subtask",
                "target": "SELF",
                "proposed_points": 200,
                "operation_key": "api-subtask",
                "parent_task_id": epic["id"],
                "parent_version": approved.json()["version"],
            },
        )
        assert child_response.status_code == 201, child_response.text
        child = child_response.json()
        assert child["parent_task_id"] == epic["id"]

        detail = await client.get(f"/api/miniapp/tasks/{epic['id']}", headers=headers_max)
        assert detail.status_code == 200
        assert detail.json()["subtask_summary"] == {
            "count": 1,
            "done_count": 0,
            "agreed_points": 0,
            "progress": 0,
        }
        listing = await client.get("/api/miniapp/tasks?scope=mine&filter=all", headers=headers_max)
        listed_epic = next(row for row in listing.json()["items"] if row["id"] == epic["id"])
        assert listed_epic["subtask_summary"]["count"] == 1
        assert all(row["id"] != child["id"] for row in listing.json()["items"])

        existing_response = await client.post(
            "/api/miniapp/tasks",
            headers=headers_max,
            json={"title": "Existing", "target": "SELF", "operation_key": "api-existing"},
        )
        existing = existing_response.json()
        existing_approved = await client.post(
            f"/api/miniapp/tasks/{existing['id']}/actions",
            headers=headers_vika,
            json={"version": existing["version"], "status": "RATE", "points": 300},
        )
        completed = await client.post(
            f"/api/miniapp/tasks/{existing['id']}/actions",
            headers=headers_max,
            json={"version": existing_approved.json()["version"], "status": "DONE"},
        )
        attachable = await client.get(
            f"/api/miniapp/tasks/{epic['id']}/attachable", headers=headers_max
        )
        assert any(row["id"] == existing["id"] for row in attachable.json()["items"])
        parent = (await client.get(f"/api/miniapp/tasks/{epic['id']}", headers=headers_max)).json()
        attached = await client.post(
            f"/api/miniapp/tasks/{epic['id']}/subtasks/{existing['id']}",
            headers=headers_max,
            json={
                "version": parent["version"],
                "child_version": completed.json()["version"],
                "operation_key": "api-attach",
            },
        )
        assert attached.status_code == 200, attached.text
        parent = (await client.get(f"/api/miniapp/tasks/{epic['id']}", headers=headers_max)).json()
        assert parent["subtask_summary"]["done_count"] == 1
        assert parent["subtask_summary"]["agreed_points"] == 300

        detached = await client.request(
            "DELETE",
            f"/api/miniapp/tasks/{epic['id']}/subtasks/{existing['id']}",
            headers=headers_max,
            json={
                "version": parent["version"],
                "child_version": attached.json()["version"],
                "operation_key": "api-detach",
            },
        )
        assert detached.status_code == 200, detached.text

        parent = (await client.get(f"/api/miniapp/tasks/{epic['id']}", headers=headers_max)).json()
        link = await client.post(
            f"/api/miniapp/tasks/{epic['id']}/links",
            headers=headers_max,
            json={
                "target_task_id": existing["id"],
                "target_version": detached.json()["version"],
                "version": parent["version"],
                "kind": "RELATED",
                "operation_key": "api-related-link",
            },
        )
        assert link.status_code == 201, link.text
        parent = (await client.get(f"/api/miniapp/tasks/{epic['id']}", headers=headers_max)).json()
        assert parent["links"][0]["title"] == "Existing"
        removed = await client.request(
            "DELETE",
            f"/api/miniapp/task-links/{link.json()['id']}",
            headers=headers_max,
            json={"version": link.json()["version"], "operation_key": "api-unlink"},
        )
        assert removed.status_code == 200, removed.text


async def test_volume_templates_and_weekly_trend_api(factory, settings) -> None:
    app = create_app(settings, session_factory=factory, workers=False)
    headers_max = {"Authorization": f"tma {init_data(settings, settings.user_max_id)}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        template_response = await client.post(
            "/api/miniapp/templates",
            headers=headers_max,
            json={
                "title": "Тренировка",
                "points": 100,
                "estimated_minutes": 60,
                "pricing_mode": "PER_TIME",
                "base_quantity": 20,
                "operation_key": "volume-template-api",
            },
        )
        assert template_response.status_code == 201, template_response.text
        template = template_response.json()
        assert template["pricing_mode"] == "PER_TIME" and template["base_quantity"] == 20

        task_response = await client.post(
            "/api/miniapp/tasks",
            headers=headers_max,
            json={
                "title": "Тренировка",
                "proposed_points": 300,
                "estimated_minutes": 60,
                "template_id": template["id"],
                "template_version": template["version"],
                "template_quantity": 60,
                "operation_key": "volume-task-api",
            },
        )
        assert task_response.status_code == 201, task_response.text
        task = task_response.json()
        assert task["status"] == "TODO" and task["points"] == 300
        assert task["template_snapshot"]["quantity"] == 60

        started = await client.post(
            f"/api/miniapp/tasks/{task['id']}/actions",
            headers=headers_max,
            json={"version": task["version"], "status": "IN_PROGRESS"},
        )
        assert started.status_code == 200, started.text
        completed = await client.post(
            f"/api/miniapp/tasks/{task['id']}/actions",
            headers=headers_max,
            json={"version": started.json()["version"], "status": "DONE"},
        )
        assert completed.status_code == 200, completed.text

        trend = await client.get("/api/miniapp/results/trend?weeks=4", headers=headers_max)
        assert trend.status_code == 200
        rows = trend.json()["weeks"]
        assert len(rows) == 4 and rows == sorted(rows, key=lambda row: row["start_date"])
        max_score = next(
            score for score in rows[-1]["scores"] if score["name"] == settings.user_max_name
        )
        assert max_score["earned"] == 300 and max_score["net"] == 300
