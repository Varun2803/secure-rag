from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.auth.security import create_access_token, hash_password
from app.config import Settings
from app.tenants.models import Tenant, TenantMembership, TenantRole
from app.users.models import User

_TOKEN_SECRET = "context-test-secret-with-more-than-32-bytes"


def _create_user(test_session_factory, email: str) -> User:
    with test_session_factory() as session:
        user = User(email=email, hashed_password=hash_password("ContextPassword123!"), full_name="Context User")
        session.add(user)
        session.commit()
        session.refresh(user)
        return user


def _token(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def test_context_contains_verified_user_tenant_membership_and_role(
    auth_app,
    auth_client: TestClient,
    test_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.auth.security.get_settings",
        lambda: Settings(secret_key=SecretStr(_TOKEN_SECRET)),
    )
    owner = _create_user(test_session_factory, "owner@example.com")
    member = _create_user(test_session_factory, "member@example.com")
    tenant_response = auth_client.post(
        "/api/v1/tenants", json={"name": "Context Co", "slug": "context-co"}, headers=_token(owner)
    )
    tenant_id = UUID(tenant_response.json()["id"])
    membership_response = auth_client.post(
        f"/api/v1/tenants/{tenant_id}/memberships",
        json={"email": member.email, "role": "MANAGER"},
        headers=_token(owner),
    )
    response = auth_client.get(f"/__test/tenant-context/{tenant_id}", headers=_token(member))
    with test_session_factory() as session:
        tenant = session.get(Tenant, tenant_id)
        membership = session.get(TenantMembership, UUID(membership_response.json()["id"]))
    assert response.status_code == 200
    assert response.json() == {
        "user_id": str(member.id),
        "tenant_id": str(tenant.id),
        "membership_id": str(membership.id),
        "role": TenantRole.MANAGER.value,
    }


@pytest.mark.parametrize("inactive_tenant,inactive_membership", [(False, True), (True, False)])
def test_context_rejects_inactive_membership_or_tenant(
    auth_app,
    auth_client: TestClient,
    test_session_factory,
    monkeypatch: pytest.MonkeyPatch,
    inactive_tenant: bool,
    inactive_membership: bool,
) -> None:
    monkeypatch.setattr(
        "app.auth.security.get_settings",
        lambda: Settings(secret_key=SecretStr(_TOKEN_SECRET)),
    )
    owner = _create_user(test_session_factory, "owner@example.com")
    response = auth_client.post(
        "/api/v1/tenants", json={"name": "Context Co", "slug": "context-co"}, headers=_token(owner)
    )
    tenant_id = UUID(response.json()["id"])
    with test_session_factory() as session:
        tenant = session.get(Tenant, tenant_id)
        membership = session.query(TenantMembership).filter_by(tenant_id=tenant_id).one()
        tenant.is_active = not inactive_tenant
        membership.is_active = not inactive_membership
        session.commit()
    rejected = auth_client.get(f"/__test/tenant-context/{tenant_id}", headers=_token(owner))
    assert rejected.status_code == 404


def test_context_rejects_cross_tenant_access(
    auth_app,
    auth_client: TestClient,
    test_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.auth.security.get_settings",
        lambda: Settings(secret_key=SecretStr(_TOKEN_SECRET)),
    )
    alice = _create_user(test_session_factory, "alice@example.com")
    bob = _create_user(test_session_factory, "bob@example.com")
    response = auth_client.post(
        "/api/v1/tenants", json={"name": "Alice Co", "slug": "alice-co"}, headers=_token(alice)
    )
    denied = auth_client.get(f"/__test/tenant-context/{response.json()['id']}", headers=_token(bob))
    assert denied.status_code == 404


def test_context_rejects_inactive_authenticated_user(
    auth_client: TestClient,
    test_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.auth.security.get_settings",
        lambda: Settings(secret_key=SecretStr(_TOKEN_SECRET)),
    )
    owner = _create_user(test_session_factory, "owner@example.com")
    tenant_response = auth_client.post(
        "/api/v1/tenants", json={"name": "Inactive Co", "slug": "inactive-co"}, headers=_token(owner)
    )
    tenant_id = tenant_response.json()["id"]
    with test_session_factory() as session:
        persisted_user = session.get(User, owner.id)
        persisted_user.is_active = False
        session.commit()
    response = auth_client.get(f"/__test/tenant-context/{tenant_id}", headers=_token(owner))
    assert response.status_code == 401
