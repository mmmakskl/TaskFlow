"""Production PID 1: validate configuration, migrate safely, then replace with Uvicorn."""

import logging
import os
import sys

from app.config.logging import configure_logging
from app.config.settings import Settings
from app.db.migrate import main as migrate


def main() -> None:
    settings = Settings()
    configure_logging(settings)
    logger = logging.getLogger(__name__)
    try:
        port = int(os.environ.get("PORT", "10000"))
        if not 1 <= port <= 65535:
            raise ValueError("PORT outside valid range")
        logger.info("migration_start")
        migrate()
        logger.info("migration_complete")
    except Exception:
        logger.exception("startup_failed")
        raise SystemExit(1) from None
    os.execvp(
        sys.executable,
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            str(port),
            "--timeout-graceful-shutdown",
            "30",
            "--no-access-log",
        ],
    )


if __name__ == "__main__":
    main()
