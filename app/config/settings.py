from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)
    bot_token: SecretStr
    webhook_secret: SecretStr
    webhook_base_url: str = ""
    database_url: SecretStr
    user_max_id: int
    user_max_name: str = "Макс"
    user_vika_id: int
    user_vika_name: str = "Вика"
    default_timezone: str = "Europe/Helsinki"
    default_weekly_target: int = 10000
    rating_notification_cooldown_minutes: int = 60
    rating_notification_debounce_seconds: int = 5
    environment: str = "production"
    log_level: str = "INFO"
    register_webhook: bool = True

    @field_validator("default_timezone")
    @classmethod
    def timezone_exists(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @model_validator(mode="after")
    def validate_settings(self) -> "Settings":
        if self.user_max_id <= 0 or self.user_vika_id <= 0:
            raise ValueError("Telegram IDs must be positive")
        if self.user_max_id == self.user_vika_id:
            raise ValueError("Two distinct Telegram accounts are required")
        if not 1 <= self.default_weekly_target <= 1000000:
            raise ValueError("Weekly target must be 1..1000000")
        if self.rating_notification_cooldown_minutes < 60:
            raise ValueError("Rating cooldown cannot be less than 60 minutes")
        if not 1 <= self.rating_notification_debounce_seconds <= 60:
            raise ValueError("Debounce must be 1..60 seconds")
        secret = self.webhook_secret.get_secret_value()
        if not 32 <= len(secret) <= 256 or any(
            ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
            for ch in secret
        ):
            raise ValueError("Webhook secret must be 32..256 URL-safe characters")
        url = make_url(self.database_url.get_secret_value())
        if url.get_backend_name() not in ("postgresql", "postgres"):
            raise ValueError("Only PostgreSQL is supported")
        if self.register_webhook and not self.webhook_base_url.startswith("https://"):
            raise ValueError("Webhook base URL must use HTTPS")
        if self.environment == "production" and not self.register_webhook:
            raise ValueError("Production requires Telegram webhook registration")
        if self.register_webhook:
            base = urlsplit(self.webhook_base_url)
            if (
                not base.hostname
                or base.username
                or base.password
                or base.path not in ("", "/")
                or base.query
                or base.fragment
            ):
                raise ValueError(
                    "Webhook base URL must be an HTTPS origin without credentials/path/query"
                )
        if len(self.user_max_name) > 50 or len(self.user_vika_name) > 50:
            raise ValueError("Display names must be at most 50 characters")
        return self

    @property
    def authorized_ids(self) -> frozenset[int]:
        return frozenset((self.user_max_id, self.user_vika_id))

    @property
    def async_database_url(self) -> str:
        url = make_url(self.database_url.get_secret_value())
        query = dict(url.query)
        sslmode = query.pop("sslmode", None)
        if sslmode:
            query["ssl"] = sslmode
        return url.set(drivername="postgresql+asyncpg", query=query).render_as_string(
            hide_password=False
        )
