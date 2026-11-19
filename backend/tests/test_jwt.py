from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
import pytest
from jwt.exceptions import InvalidTokenError
from pydantic import SecretStr

from app.auth.security import ALGORITHM, TokenConfigurationError, create_access_token, decode_access_token
from app.config import Settings

_SECRET = "test-secret-key-with-at-least-32-bytes-long"


@pytest.fixture(autouse=True)
def jwt_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(secret_key=SecretStr(_SECRET), jwt_issuer="test-issuer", jwt_audience="test-audience")
    monkeypatch.setattr("app.auth.security.get_settings", lambda: settings)


def test_valid_token() -> None:
    subject = uuid4()
    assert decode_access_token(create_access_token(subject)) == subject


@pytest.mark.parametrize("token", ["not-a-jwt", "", "a.b.c"])
def test_malformed_token(token: str) -> None:
    with pytest.raises(InvalidTokenError):
        decode_access_token(token)


def _token(**claims: object) -> str:
    now = datetime.now(timezone.utc)
    base: dict[str, object] = {
        "sub": str(uuid4()), "iat": now, "exp": now + timedelta(minutes=5),
        "jti": str(uuid4()), "type": "access", "iss": "test-issuer", "aud": "test-audience",
    }
    base.update(claims)
    return jwt.encode(base, _SECRET, algorithm=ALGORITHM)


@pytest.mark.parametrize("claims", [
    {"sub": str(uuid4()), "iat": datetime.now(timezone.utc) - timedelta(hours=2),
     "exp": datetime.now(timezone.utc) - timedelta(hours=1)},
    {"iss": "wrong-issuer"}, {"aud": "wrong-audience"}, {"type": "refresh"},
    {"sub": "not-a-uuid"}, {"sub": None},
])
def test_rejects_expired_or_invalid_claims(claims: dict[str, object]) -> None:
    with pytest.raises(InvalidTokenError):
        decode_access_token(_token(**claims))


def test_rejects_missing_subject() -> None:
    token = _token()
    claims = jwt.decode(token, _SECRET, algorithms=[ALGORITHM], options={"verify_exp": False, "verify_aud": False})
    claims.pop("sub")
    unsigned_claims = jwt.encode(claims, _SECRET, algorithm=ALGORITHM)
    with pytest.raises(InvalidTokenError):
        decode_access_token(unsigned_claims)


def test_rejects_invalid_signature() -> None:
    token = create_access_token(uuid4())
    tampered = jwt.encode({"sub": str(uuid4())}, "different-signing-secret-1234567890", algorithm=ALGORITHM)
    assert token != tampered
    with pytest.raises(InvalidTokenError):
        decode_access_token(tampered)


def test_rejects_unsupported_algorithm() -> None:
    token = jwt.encode({"sub": str(uuid4())}, key="", algorithm="none")
    with pytest.raises(InvalidTokenError):
        decode_access_token(token)


def test_requires_strong_configured_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.auth.security.get_settings", lambda: Settings(secret_key=SecretStr("too-short")))
    with pytest.raises(TokenConfigurationError):
        create_access_token(uuid4())


def test_rejects_repetitive_configured_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.auth.security.get_settings", lambda: Settings(secret_key=SecretStr("a" * 48)))
    with pytest.raises(TokenConfigurationError):
        create_access_token(uuid4())
