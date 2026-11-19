from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import jwt
from jwt.exceptions import InvalidTokenError
from pwdlib import PasswordHash
from pwdlib.exceptions import PwdlibError

from app.config import get_settings

_password_hash = PasswordHash.recommended()
_DUMMY_HASH = _password_hash.hash("secure-rag-dummy-password-for-timing")
ALGORITHM = "HS256"


class TokenConfigurationError(RuntimeError):
    """Raised when the JWT signing secret is absent or weak."""


def validate_password(password: str) -> str:
    if not password or not password.strip() or len(password) < 12 or len(password) > 128:
        raise ValueError("Password must contain 12 to 128 non-whitespace characters")
    return password


def hash_password(password: str) -> str:
    return _password_hash.hash(validate_password(password))


def verify_password(password: str, hashed_password: str | None) -> bool:
    if not isinstance(password, str) or not password or len(password) > 128:
        return False
    candidate = hashed_password or _DUMMY_HASH
    try:
        valid, _ = _password_hash.verify_and_update(password, candidate)
        return bool(valid and hashed_password)
    except (PwdlibError, ValueError, TypeError):
        return False


def _signing_secret() -> str:
    configured_secret = get_settings().secret_key
    secret = configured_secret.get_secret_value() if configured_secret else ""
    if len(secret.encode("utf-8")) < 32 or len(set(secret)) < 8:
        raise TokenConfigurationError("JWT signing secret must be at least 32 bytes and nontrivial")
    return secret


def create_access_token(subject: UUID, *, now: datetime | None = None) -> str:
    settings = get_settings()
    issued_at = now or datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(minutes=settings.access_token_expire_minutes)
    claims = {
        "sub": str(subject),
        "iat": issued_at,
        "exp": expires_at,
        "jti": str(uuid4()),
        "type": "access",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }
    return jwt.encode(claims, _signing_secret(), algorithm=ALGORITHM)


def decode_access_token(token: str) -> UUID:
    settings = get_settings()
    claims = jwt.decode(
        token,
        _signing_secret(),
        algorithms=[ALGORITHM],
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        options={"require": ["sub", "iat", "exp", "jti", "type", "iss", "aud"]},
    )
    token_id = claims.get("jti")
    if claims.get("type") != "access" or not isinstance(token_id, str) or not token_id:
        raise InvalidTokenError("Invalid access token claims")
    try:
        return UUID(claims["sub"])
    except (ValueError, TypeError, AttributeError) as exc:
        raise InvalidTokenError("Invalid token subject") from exc
