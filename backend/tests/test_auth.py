from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.auth.security import create_access_token, hash_password
from app.config import Settings
from app.users.models import User


def test_successful_registration_normalizes_email_and_hides_hash(auth_client: TestClient) -> None:
    response = auth_client.post("/api/v1/auth/register", json={
        "email": "  USER@Example.com ", "password": "StrongPassword123!", "full_name": "Example User",
    })
    assert response.status_code == 201
    assert response.json()["email"] == "user@example.com"
    assert "hashed_password" not in response.json()
    assert "password" not in response.json()


def test_duplicate_registration_returns_conflict(auth_client: TestClient) -> None:
    payload = {"email": "user@example.com", "password": "StrongPassword123!", "full_name": "Example User"}
    assert auth_client.post("/api/v1/auth/register", json=payload).status_code == 201
    response = auth_client.post("/api/v1/auth/register", json={**payload, "email": "USER@example.com"})
    assert response.status_code == 409
    assert response.json()["detail"] == "An account with this email already exists"


def test_registration_validation_does_not_echo_password(auth_client: TestClient) -> None:
    response = auth_client.post("/api/v1/auth/register", json={
        "email": "invalid-email", "password": "weak", "full_name": "Example",
    })
    assert response.status_code == 422
    assert "weak" not in response.text
    assert "invalid-email" not in response.text


def test_registration_requires_fields(auth_client: TestClient) -> None:
    assert auth_client.post("/api/v1/auth/register", json={}).status_code == 422


def test_password_hash_is_persisted(auth_client: TestClient, test_session_factory) -> None:
    auth_client.post("/api/v1/auth/register", json={
        "email": "user@example.com", "password": "StrongPassword123!", "full_name": "Example User",
    })
    with test_session_factory() as session:
        user = session.query(User).filter_by(email="user@example.com").one()
    assert user.hashed_password != "StrongPassword123!"
    assert user.hashed_password.startswith("$argon2id$")


def _add_user(test_session_factory, *, active: bool = True, superuser: bool = False) -> User:
    with test_session_factory() as session:
        user = User(id=uuid4(), email="user@example.com", hashed_password=hash_password("StrongPassword123!"),
                    full_name="Example User", is_active=active, is_superuser=superuser)
        session.add(user)
        session.commit()
        session.refresh(user)
        return user


def test_successful_login_returns_oauth_token_response(auth_client: TestClient, test_session_factory, monkeypatch) -> None:
    _add_user(test_session_factory)
    monkeypatch.setattr("app.auth.security.get_settings", lambda: Settings(
        secret_key=SecretStr("test-secret-key-with-at-least-32-bytes-long")))
    response = auth_client.post("/api/v1/auth/login", data={"username": "user@example.com", "password": "StrongPassword123!"})
    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"
    assert response.json()["expires_in"] == 1800
    assert response.json()["access_token"]


def test_login_invalid_credentials_are_generic(auth_client: TestClient, test_session_factory) -> None:
    _add_user(test_session_factory)
    wrong_password = auth_client.post("/api/v1/auth/login", data={"username": "user@example.com", "password": "wrong"})
    unknown_email = auth_client.post("/api/v1/auth/login", data={"username": "missing@example.com", "password": "wrong"})
    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()


def test_inactive_user_cannot_login(auth_client: TestClient, test_session_factory) -> None:
    _add_user(test_session_factory, active=False)
    response = auth_client.post("/api/v1/auth/login", data={"username": "user@example.com", "password": "StrongPassword123!"})
    unknown = auth_client.post("/api/v1/auth/login", data={"username": "missing@example.com", "password": "StrongPassword123!"})
    assert response.status_code == 401
    assert response.json() == unknown.json() == {"detail": "Incorrect email or password"}


def test_me_requires_valid_token_and_reads_database_user(auth_client: TestClient, test_session_factory, monkeypatch) -> None:
    user = _add_user(test_session_factory)
    monkeypatch.setattr("app.auth.security.get_settings", lambda: Settings(
        secret_key=SecretStr("test-secret-key-with-at-least-32-bytes-long")))
    token = create_access_token(user.id)
    response = auth_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["id"] == str(user.id)
    assert "hashed_password" not in response.json()


def test_missing_and_invalid_bearer_tokens_are_rejected(
    auth_client: TestClient, monkeypatch
) -> None:
    monkeypatch.setattr("app.auth.security.get_settings", lambda: Settings(
        secret_key=SecretStr("test-secret-key-with-at-least-32-bytes-long")))
    assert auth_client.get("/api/v1/auth/me").status_code == 401
    assert auth_client.get("/api/v1/auth/me", headers={"Authorization": "Bearer invalid"}).status_code == 401


def test_deleted_and_inactive_users_are_rejected(auth_client: TestClient, test_session_factory, monkeypatch) -> None:
    user = _add_user(test_session_factory)
    monkeypatch.setattr("app.auth.security.get_settings", lambda: Settings(
        secret_key=SecretStr("test-secret-key-with-at-least-32-bytes-long")))
    token = create_access_token(user.id)
    with test_session_factory() as session:
        session.delete(session.get(User, user.id))
        session.commit()
    assert auth_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401

    inactive = _add_user(test_session_factory, active=False)
    inactive_token = create_access_token(inactive.id)
    assert auth_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {inactive_token}"}).status_code == 401


def test_superuser_dependency(auth_app, test_session_factory, monkeypatch) -> None:
    from typing import Annotated
    from fastapi import Depends
    from fastapi.testclient import TestClient
    from app.auth.dependencies import require_superuser
    from app.users.schemas import UserPublic

    @auth_app.get("/test/superuser", response_model=UserPublic)
    def superuser_route(user: Annotated[User, Depends(require_superuser)]) -> User:
        return user

    monkeypatch.setattr("app.auth.security.get_settings", lambda: Settings(
        secret_key=SecretStr("test-secret-key-with-at-least-32-bytes-long")))
    regular = _add_user(test_session_factory)
    regular_token = create_access_token(regular.id)
    admin = _add_user_with_email(test_session_factory, "admin@example.com", superuser=True)
    admin_token = create_access_token(admin.id)
    with TestClient(auth_app) as client:
        assert client.get("/test/superuser", headers={"Authorization": f"Bearer {regular_token}"}).status_code == 403
        assert client.get("/test/superuser", headers={"Authorization": f"Bearer {admin_token}"}).status_code == 200


def _add_user_with_email(test_session_factory, email: str, *, superuser: bool) -> User:
    with test_session_factory() as session:
        user = User(id=uuid4(), email=email, hashed_password=hash_password("StrongPassword123!"),
                    full_name="Example User", is_active=True, is_superuser=superuser)
        session.add(user)
        session.commit()
        session.refresh(user)
        return user
