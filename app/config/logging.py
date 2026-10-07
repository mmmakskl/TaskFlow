import logging
import re
import traceback
from types import TracebackType

from sqlalchemy.exc import DBAPIError

from app.config.settings import Settings


class RedactingFormatter(logging.Formatter):
    def __init__(self, secrets: list[str]) -> None:
        super().__init__("%(asctime)s level=%(levelname)s logger=%(name)s %(message)s")
        self.secrets = [secret for secret in secrets if secret]

    def formatException(
        self,
        ei: tuple[type[BaseException], BaseException, TracebackType | None]
        | tuple[None, None, None],
    ) -> str:
        error = ei[1]
        if isinstance(error, DBAPIError):
            # PostgreSQL DETAIL can contain a complete private failing row even with
            # SQLAlchemy hide_parameters. Preserve frames and SQLSTATE, never server text.
            state = getattr(error.orig, "sqlstate", "unknown")
            state = (
                state
                if isinstance(state, str) and re.fullmatch(r"[0-9A-Z]{5}", state)
                else "unknown"
            )
            frames = "".join(traceback.format_list(traceback.extract_tb(ei[2])))
            return f"{frames}{type(error).__name__}: database operation failed sqlstate={state}"
        return super().formatException(ei)

    def format(self, record: logging.LogRecord) -> str:
        result = super().format(record)
        for secret in self.secrets:
            result = result.replace(secret, "[REDACTED]")
        result = re.sub(r"(?:postgres(?:ql)?(?:\+asyncpg)?://)[^\s]+", "[DATABASE_URL]", result)
        result = re.sub(r"\b\d{5,}:[A-Za-z0-9_-]{20,}", "[BOT_TOKEN]", result)
        return result


def configure_logging(settings: Settings) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        RedactingFormatter(
            [
                settings.bot_token.get_secret_value(),
                settings.webhook_secret.get_secret_value(),
                settings.database_url.get_secret_value(),
            ]
        )
    )
    logging.basicConfig(level=settings.log_level, handlers=[handler], force=True)
    # Uvicorn installs its own non-propagating handlers before importing the app.
    # ASGI failures must use the same redaction as worker and startup failures.
    for name in ("uvicorn", "uvicorn.error"):
        server_logger = logging.getLogger(name)
        server_logger.handlers = []
        server_logger.propagate = True
    access_logger = logging.getLogger("uvicorn.access")
    access_enabled = bool(access_logger.handlers) or access_logger.propagate
    access_logger.handlers = []
    access_logger.propagate = access_enabled
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
