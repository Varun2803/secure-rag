from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.auth.security import TokenConfigurationError, decode_access_token
from app.audit.dependencies import record_request_event
from app.audit.events import AuditEventType, AuditOutcome
from app.config import get_settings
from app.database.postgres import get_db
from app.users.models import User
from app.users.repository import get_user_by_id

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{get_settings().api_v1_prefix}/auth/login")
SessionDependency = Annotated[Session, Depends(get_db)]
TokenDependency = Annotated[str, Depends(oauth2_scheme)]


def get_current_user(token: TokenDependency, session: SessionDependency, request: Request) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        user_id: UUID = decode_access_token(token)
    except TokenConfigurationError as exc:
        raise HTTPException(status_code=503, detail="Authentication is not configured") from exc
    except (jwt.InvalidTokenError, ValueError, TypeError) as exc:
        record_request_event(
            request, session, event_type=AuditEventType.AUTH_TOKEN_INVALID,
            action="auth.token.validate", outcome=AuditOutcome.DENIED,
            reason_code="INVALID_TOKEN",
        )
        raise unauthorized from exc
    user = get_user_by_id(session, user_id)
    if user is None:
        record_request_event(
            request, session, event_type=AuditEventType.AUTH_ACCESS_DENIED,
            action="auth.user.resolve", outcome=AuditOutcome.DENIED,
            reason_code="USER_NOT_FOUND",
        )
        raise unauthorized
    if not user.is_active:
        record_request_event(
            request, session, event_type=AuditEventType.AUTH_ACCESS_DENIED,
            action="auth.user.resolve", outcome=AuditOutcome.DENIED,
            actor_user_id=user.id, reason_code="USER_INACTIVE",
        )
        raise unauthorized
    return user


def get_current_active_user(user: Annotated[User, Depends(get_current_user)]) -> User:
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Inactive user")
    return user


def require_superuser(
    user: Annotated[User, Depends(get_current_active_user)], session: SessionDependency, request: Request,
) -> User:
    if not user.is_superuser:
        record_request_event(
            request, session, event_type=AuditEventType.AUTH_ACCESS_DENIED,
            action="auth.superuser.authorize", outcome=AuditOutcome.DENIED,
            actor_user_id=user.id, reason_code="SUPERUSER_REQUIRED",
        )
        raise HTTPException(status_code=403, detail="Insufficient privileges")
    return user
