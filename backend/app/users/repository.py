from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.users.models import User


def get_user_by_email(session: Session, email: str) -> User | None:
    normalized_email = User.normalize_email(email)
    return session.scalar(select(User).where(User.email == normalized_email))


def get_user_by_id(session: Session, user_id: UUID) -> User | None:
    return session.get(User, user_id)


def create_user(session: Session, *, email: str, hashed_password: str, full_name: str) -> User:
    user = User(email=User.normalize_email(email), hashed_password=hashed_password, full_name=full_name)
    session.add(user)
    return user
