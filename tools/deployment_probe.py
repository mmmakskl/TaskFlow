"""HTTPS/Telegram/PostgreSQL deployment receipts without exposing secrets or message content."""

import argparse
import asyncio
import hashlib
import json
import secrets
from pathlib import Path

import httpx
from aiogram import Bot
from dotenv import dotenv_values
from sqlalchemy import text

from app.config.settings import Settings
from app.db.session import database

TABLES = ("tasks", "penalties", "penalty_types", "audit_events", "score_events")


def write_receipt(output: Path, content: str) -> None:
    output.touch(mode=0o600, exist_ok=True)
    output.chmod(0o600)
    output.write_text(content)


async def probe(args: argparse.Namespace) -> None:
    values = dotenv_values(args.env_file)
    settings = Settings(**{k.lower(): v for k, v in values.items()}, _env_file=None)
    base = settings.webhook_base_url.rstrip("/")
    engine, factory = database(settings)
    receipt = {}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            for path, expected in (("/health", {"status": "ok"}), ("/ready", {"status": "ready"})):
                response = await client.get(base + path)
                assert response.status_code == 200 and response.json() == expected, path
            assert (
                await client.post(base + "/telegram/webhook", json={"update_id": 1})
            ).status_code == 403
            if args.exercise_webhook:
                # Negative IDs are reserved for synthetic transport probes, never real user actions.
                marker = -secrets.randbelow(2**50) - 1
                headers = {
                    "X-Telegram-Bot-Api-Secret-Token": settings.webhook_secret.get_secret_value()
                }
                for _ in range(2):
                    response = await client.post(
                        base + "/telegram/webhook", json={"update_id": marker}, headers=headers
                    )
                    assert response.status_code == 200, "signed webhook rejected"
                for _ in range(40):
                    async with factory() as session:
                        state = await session.scalar(
                            text(
                                "SELECT status FROM taskflow.telegram_updates WHERE update_id=:id"
                            ),
                            {"id": marker},
                        )
                    if state == "done":
                        break
                    await asyncio.sleep(0.25)
                assert state == "done", "worker did not process webhook marker"
                receipt["webhook_marker"] = marker
        async with factory() as session:
            revision = await session.scalar(text("SELECT version_num FROM public.alembic_version"))
            assert revision == args.revision, "unexpected Alembic revision"
            receipt["revision"] = revision
            receipt["business"] = {}
            for table in TABLES:
                rows = list(
                    await session.scalars(
                        text("SELECT to_jsonb(t)::text FROM taskflow." + table + " t ORDER BY id")
                    )
                )
                receipt["business"][table] = {
                    "count": len(rows),
                    "sha256": hashlib.sha256("\n".join(rows).encode()).hexdigest(),
                }
            if args.baseline:
                baseline = json.loads(await asyncio.to_thread(Path(args.baseline).read_text))
                assert receipt["business"] == baseline.get("business", baseline), (
                    "business data changed across deployment"
                )
                if marker := baseline.get("webhook_marker"):
                    assert (
                        await session.scalar(
                            text(
                                "SELECT status FROM taskflow.telegram_updates WHERE update_id=:id"
                            ),
                            {"id": marker},
                        )
                        == "done"
                    ), "old webhook marker missing after redeploy"
        async with Bot(settings.bot_token.get_secret_value()) as bot:
            info = await bot.get_webhook_info()
            assert info.url == base + "/telegram/webhook", "Telegram webhook URL mismatch"
            assert set(info.allowed_updates or []) == {"message", "callback_query"}, (
                "unexpected Telegram update types"
            )
            receipt["telegram"] = {
                "url": info.url,
                "pending": info.pending_update_count,
                "last_error_at": info.last_error_date.isoformat() if info.last_error_date else None,
            }
        if args.receipt:
            output = Path(args.receipt)
            await asyncio.to_thread(write_receipt, output, json.dumps(receipt, indent=2))
        print(
            json.dumps(
                {
                    "health": "ok",
                    "ready": "ready",
                    "revision": revision,
                    "business_counts": {k: v["count"] for k, v in receipt["business"].items()},
                    "telegram": receipt["telegram"],
                    "persistence_compared": bool(args.baseline),
                    "signed_webhook_processed": bool(args.exercise_webhook),
                }
            )
        )
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--revision", default="0004")
    parser.add_argument("--receipt")
    parser.add_argument("--baseline")
    parser.add_argument("--exercise-webhook", action="store_true")
    args = parser.parse_args()
    try:
        asyncio.run(probe(args))
    except Exception as exc:
        # Exception text can contain a token-bearing Telegram URL or a private failing DB row.
        print(f"deployment_probe_failed type={type(exc).__name__}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
