from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth.security import hash_password
from app.users.models import User
from app.users.repository import create_user, get_user_by_email, get_user_by_id


def test_user_create_and_retrieve(test_session_factory) -> None:
    with test_session_factory() as session:
        user = create_user(session, email=" User@Example.com ",
                           hashed_password=hash_password("SecurePassword123!"), full_name="Example User")
        session.commit()
        assert user.email == "user@example.com"
        assert get_user_by_email(session, "USER@example.com") is user
        assert get_user_by_id(session, user.id) is user


def test_database_unique_email_constraint(test_session_factory) -> None:
    hashed_password = hash_password("SecurePassword123!")
    with test_session_factory() as session:
        session.add(User(email="duplicate@example.com", hashed_password=hashed_password, full_name="First"))
        session.commit()
        session.add(User(email="duplicate@example.com", hashed_password=hashed_password, full_name="Second"))
        try:
            session.commit()
            raise AssertionError("Unique email constraint was not enforced")
        except IntegrityError:
            session.rollback()
        assert session.scalar(select(User).where(User.email == "duplicate@example.com")).full_name == "First"


def test_migration_configuration() -> None:
    from alembic.config import Config
    from app.database.base import Base
    from app.users import models as _models

    config = Config("alembic.ini")
    assert config.get_main_option("script_location") == "backend/alembic"
    assert "users" in Base.metadata.tables
    assert {"tenants", "tenant_memberships"}.issubset(Base.metadata.tables)
