from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_active_user
from app.auth.security import TokenConfigurationError
from app.auth.rate_limit import enforce_auth_rate_limit
from app.auth.service import (
    DuplicateEmailError,
    InactiveUserError,
    InvalidCredentialsError,
    authenticate_user,
    register_user,
)
from app.audit.dependencies import record_request_event
from app.audit.events import AuditEventType, AuditOutcome
from app.config import get_settings
from app.database.postgres import get_db
from app.users.models import User
from app.users.schemas import TokenResponse, UserCreate, UserPublic

router = APIRouter(prefix="/auth", tags=["authentication"])
SessionDependency = Annotated[Session, Depends(get_db)]
LOGIN_LIMIT = 10
LOGIN_WINDOW_SECONDS = 60
REGISTRATION_LIMIT = 5
REGISTRATION_WINDOW_SECONDS = 3600


@router.post("/register", response_model=UserPublic, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, session: SessionDependency, request: Request) -> User:
    enforce_auth_rate_limit(
        request, session, category="register", maximum=REGISTRATION_LIMIT,
        window_seconds=REGISTRATION_WINDOW_SECONDS,
    )
    try:
        return register_user(session, email=str(payload.email), password=payload.password, full_name=payload.full_name)
    except DuplicateEmailError as exc:
        raise HTTPException(status_code=409, detail="An account with this email already exists") from exc


@router.post("/login", response_model=TokenResponse)
def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()], session: SessionDependency, request: Request,
) -> TokenResponse:
    enforce_auth_rate_limit(
        request, session, category="login", maximum=LOGIN_LIMIT,
        window_seconds=LOGIN_WINDOW_SECONDS,
    )
    try:
        user, token = authenticate_user(session, email=form.username, password=form.password)
    except InvalidCredentialsError as exc:
        record_request_event(
            request, session, event_type=AuditEventType.AUTH_LOGIN_FAILURE,
            action="auth.login", outcome=AuditOutcome.FAILURE, reason_code="INVALID_CREDENTIALS",
        )
        raise HTTPException(
            status_code=401,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except InactiveUserError as exc:
        record_request_event(
            request, session, event_type=AuditEventType.AUTH_LOGIN_FAILURE,
            action="auth.login", outcome=AuditOutcome.FAILURE, reason_code="INACTIVE_ACCOUNT",
        )
        raise HTTPException(
            status_code=401,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except TokenConfigurationError as exc:
        raise HTTPException(status_code=503, detail="Authentication is not configured") from exc
    record_request_event(
        request, session, event_type=AuditEventType.AUTH_LOGIN_SUCCESS,
        action="auth.login", outcome=AuditOutcome.SUCCESS, actor_user_id=user.id,
    )
    return TokenResponse(access_token=token, expires_in=get_settings().access_token_expire_minutes * 60)


@router.get("/me", response_model=UserPublic)
def read_current_user(user: Annotated[User, Depends(get_current_active_user)]) -> User:
    return user
