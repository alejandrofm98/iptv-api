import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from iptv_api.core.dependencies import get_current_user, require_auth_with_jwt
from iptv_api.core.exceptions import UnauthorizedException
from iptv_api.routers.auth import ALGORITHM, SECRET_KEY, create_access_token
from iptv_api.routers.replays import validate_stream_token


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _legacy_hs256_token(payload: dict) -> str:
    """Construye un JWT compacto HS256 con el formato emitido por python-jose."""
    header = _b64url(b'{"alg":"HS256","typ":"JWT"}')
    claims = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header}.{claims}"
    signature = hmac.new(SECRET_KEY.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url(signature)}"


def test_created_access_token_keeps_hs256_claim_contract() -> None:
    token = create_access_token({"sub": "user-123", "role": "admin"})

    assert jwt.get_unverified_header(token)["alg"] == "HS256"
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    assert payload["sub"] == "user-123"
    assert payload["role"] == "admin"
    assert validate_stream_token(token) == {"id": "user-123", "role": "admin"}


def test_invalid_replay_token_keeps_unauthorized_response() -> None:
    with pytest.raises(UnauthorizedException):
        validate_stream_token("not-a-jwt")


@pytest.mark.asyncio
async def test_legacy_python_jose_hs256_token_is_accepted() -> None:
    token = _legacy_hs256_token(
        {
            "sub": "legacy-user",
            "role": "admin",
            "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
        }
    )

    assert await get_current_user(token=token) == {"id": "legacy-user", "role": "admin"}
    assert validate_stream_token(token) == {"id": "legacy-user", "role": "admin"}


def _token_with_failure(failure: str) -> str:
    payload = {"sub": "user-123", "role": "admin"}
    if failure == "expired":
        payload["exp"] = int((datetime.now(UTC) - timedelta(minutes=1)).timestamp())
        return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    if failure == "bad_signature":
        return jwt.encode(payload, f"{SECRET_KEY}-wrong", algorithm=ALGORITHM)
    return jwt.encode(payload, SECRET_KEY, algorithm="HS384")


@pytest.mark.parametrize("validator", ["current_user", "auth_dependency", "replay"])
@pytest.mark.parametrize("failure", ["expired", "bad_signature", "bad_algorithm"])
@pytest.mark.asyncio
async def test_jwt_validators_reject_invalid_tokens(validator: str, failure: str) -> None:
    token = _token_with_failure(failure)
    with pytest.raises(UnauthorizedException):
        if validator == "current_user":
            await get_current_user(token=token)
        elif validator == "auth_dependency":
            await require_auth_with_jwt(token=token, user_svc=object())
        else:
            validate_stream_token(token)


@pytest.mark.asyncio
async def test_require_auth_with_jwt_accepts_hs256_and_returns_user() -> None:
    token = create_access_token({"sub": "user-123", "role": "user"})

    class UserServiceStub:
        def get_user(self, user_id: str) -> dict:
            assert user_id == "user-123"
            return {
                "id": user_id,
                "username": "viewer",
                "is_active": True,
                "active_devices": 1,
                "max_connections": 3,
            }

    result = await require_auth_with_jwt(token=token, user_svc=UserServiceStub())

    assert result.valid is True
    assert result.user_id == "user-123"
    assert result.username == "viewer"
