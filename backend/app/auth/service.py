from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.security import create_access_token, hash_password, verify_password
from app.users.models import User
from app.users.repository import create_user, get_user_by_email


class DuplicateEmailError(ValueError):
    """Raised when an email address is already registered."""


class InvalidCredentialsError(ValueError):
    """Raised for unknown users and incorrect passwords alike."""


class InactiveUserError(ValueError):
    """Raised when an inactive account attempts to authenticate."""


def register_user(session: Session, *, email: str, password: str, full_name: str) -> User:
    normalized_email = User.normalize_email(email)
    if get_user_by_email(session, normalized_email) is not None:
        raise DuplicateEmailError
    user = create_user(session, email=normalized_email, hashed_password=hash_password(password), full_name=full_name)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise DuplicateEmailError from exc
    session.refresh(user)
    return user


def authenticate_user(session: Session, *, email: str, password: str) -> tuple[User, str]:
    user = get_user_by_email(session, email)
    if not verify_password(password, user.hashed_password if user else None):
        raise InvalidCredentialsError
    if not user.is_active:
        raise InactiveUserError
    return user, create_access_token(user.id)
