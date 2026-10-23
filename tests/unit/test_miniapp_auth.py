import hashlib
import hmac
from urllib.parse import urlencode

import pytest

from app.web.miniapp import validate_init_data


def signed_data(token: str, user_id: int, auth_date: int) -> str:
    values = {"auth_date": str(auth_date), "user": f'{{"id":{user_id},"first_name":"Max"}}'}
    check_string = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


def test_init_data_signature_and_sender_are_verified() -> None:
    data = validate_init_data(signed_data("token", 1001, 1_700_000_000), "token", now=1_700_000_100)
    assert data["telegram_id"] == 1001
    assert data["user"]["first_name"] == "Max"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda raw: raw.replace("1001", "1002"),
        lambda raw: raw.replace("hash=", "hash=00"),
        lambda raw: raw + "&auth_date=1700000000",
    ],
)
def test_init_data_rejects_tampering_and_duplicate_fields(mutation) -> None:
    raw = signed_data("token", 1001, 1_700_000_000)
    with pytest.raises(ValueError):
        validate_init_data(mutation(raw), "token", now=1_700_000_100)


def test_init_data_rejects_expired_and_future_timestamps() -> None:
    with pytest.raises(ValueError, match="Expired"):
        validate_init_data(signed_data("token", 1001, 1_600_000_000), "token", now=1_700_000_000)
    with pytest.raises(ValueError, match="Expired"):
        validate_init_data(signed_data("token", 1001, 1_700_000_100), "token", now=1_700_000_000)
