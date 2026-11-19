from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy import create_engine, func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from app.auth.security import create_access_token, hash_password
from app.config import Settings
from app.database.base import Base
from app.tenants.models import Tenant, TenantMembership, TenantRole
from app.users.models import User

_TOKEN_SECRET = "tenant-test-secret-with-more-than-32-bytes"


def _enable_test_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.auth.security.get_settings",
        lambda: Settings(secret_key=SecretStr(_TOKEN_SECRET)),
    )


def _create_user(test_session_factory, email: str, *, active: bool = True) -> User:
    with test_session_factory() as session:
        user = User(
            id=uuid4(),
            email=email,
            hashed_password=hash_password("SecurePassword123!"),
            full_name=email.split("@", 1)[0].title(),
            is_active=active,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        return user


def _authorization(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def test_tenant_creation_normalizes_slug_and_assigns_owner(
    auth_client: TestClient, test_session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_test_tokens(monkeypatch)
    owner = _create_user(test_session_factory, "owner@example.com")
    response = auth_client.post(
        "/api/v1/tenants",
        json={"name": "  Example Company ", "slug": "Example_Company", "description": "  Team  "},
        headers=_authorization(owner),
    )
    assert response.status_code == 201
    assert response.json()["name"] == "Example Company"
    assert response.json()["slug"] == "example-company"
    assert response.json()["description"] == "Team"
    with test_session_factory() as session:
        membership = session.scalar(
            select(TenantMembership).where(TenantMembership.tenant_id == UUID(response.json()["id"]))
        )
        assert membership.user_id == owner.id
        assert membership.role == TenantRole.OWNER


def test_tenant_rejects_blank_name_and_invalid_slug(
    auth_client: TestClient, test_session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_test_tokens(monkeypatch)
    user = _create_user(test_session_factory, "owner@example.com")
    headers = _authorization(user)
    empty_name = auth_client.post("/api/v1/tenants", json={"name": "  ", "slug": "valid"}, headers=headers)
    bad_slug = auth_client.post("/api/v1/tenants", json={"name": "Valid", "slug": "bad/slug"}, headers=headers)
    assert empty_name.status_code == bad_slug.status_code == 422


def test_unique_tenant_slug_is_enforced_and_normalized(
    auth_client: TestClient, test_session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_test_tokens(monkeypatch)
    user = _create_user(test_session_factory, "owner@example.com")
    headers = _authorization(user)
    first = auth_client.post("/api/v1/tenants", json={"name": "Example", "slug": "EXAMPLE"}, headers=headers)
    second = auth_client.post("/api/v1/tenants", json={"name": "Other", "slug": "example"}, headers=headers)
    assert first.status_code == 201
    assert second.status_code == 409


def test_users_only_list_their_active_tenants(
    auth_client: TestClient, test_session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_test_tokens(monkeypatch)
    alice = _create_user(test_session_factory, "alice@example.com")
    bob = _create_user(test_session_factory, "bob@example.com")
    created = auth_client.post(
        "/api/v1/tenants", json={"name": "Alice Company", "slug": "alice-company"},
        headers=_authorization(alice),
    )
    listed = auth_client.get("/api/v1/tenants", headers=_authorization(bob))
    assert created.status_code == 201
    assert listed.status_code == 200
    assert listed.json() == []


def test_one_user_can_hold_distinct_roles_in_multiple_tenants(
    auth_client: TestClient, test_session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_test_tokens(monkeypatch)
    owner = _create_user(test_session_factory, "owner@example.com")
    member = _create_user(test_session_factory, "member@example.com")
    headers = _authorization(owner)
    first = auth_client.post(
        "/api/v1/tenants", json={"name": "First Co", "slug": "first-co"}, headers=headers
    ).json()
    second = auth_client.post(
        "/api/v1/tenants", json={"name": "Second Co", "slug": "second-co"}, headers=headers
    ).json()
    for tenant, role in ((first, "HR"), (second, "MANAGER")):
        response = auth_client.post(
            f"/api/v1/tenants/{tenant['id']}/memberships",
            json={"email": member.email, "role": role},
            headers=headers,
        )
        assert response.status_code == 201
    listed = auth_client.get("/api/v1/tenants", headers=_authorization(member))
    assert {item["role"] for item in listed.json()} == {"HR", "MANAGER"}
    assert len(listed.json()) == 2


def test_tenant_access_requires_active_membership_and_does_not_trust_id(
    auth_client: TestClient, test_session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_test_tokens(monkeypatch)
    owner = _create_user(test_session_factory, "owner@example.com")
    outsider = _create_user(test_session_factory, "outsider@example.com")
    created = auth_client.post(
        "/api/v1/tenants", json={"name": "Private Company", "slug": "private-company"},
        headers=_authorization(owner),
    )
    tenant_id = created.json()["id"]
    assert auth_client.get(f"/api/v1/tenants/{tenant_id}", headers=_authorization(outsider)).status_code == 404
    assert auth_client.get(f"/api/v1/tenants/{uuid4()}", headers=_authorization(owner)).status_code == 404
    with test_session_factory() as session:
        membership = session.scalar(
            select(TenantMembership).where(TenantMembership.tenant_id == UUID(tenant_id))
        )
        membership.is_active = False
        session.commit()
    assert auth_client.get(f"/api/v1/tenants/{tenant_id}", headers=_authorization(owner)).status_code == 404


def test_membership_roles_and_admin_role_limits(
    auth_client: TestClient, test_session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_test_tokens(monkeypatch)
    owner = _create_user(test_session_factory, "owner@example.com")
    employee = _create_user(test_session_factory, "employee@example.com")
    tenant = auth_client.post(
        "/api/v1/tenants", json={"name": "Company", "slug": "company"},
        headers=_authorization(owner),
    ).json()
    added = auth_client.post(
        f"/api/v1/tenants/{tenant['id']}/memberships",
        json={"email": employee.email, "role": "ADMIN"},
        headers=_authorization(owner),
    )
    assert added.status_code == 201
    assert added.json()["role"] == "ADMIN"
    assert "hashed_password" not in added.json()["user"]

    another = _create_user(test_session_factory, "another@example.com")
    denied = auth_client.post(
        f"/api/v1/tenants/{tenant['id']}/memberships",
        json={"email": another.email, "role": "OWNER"},
        headers=_authorization(employee),
    )
    assert denied.status_code == 403


def test_duplicate_membership_and_reactivation(
    auth_client: TestClient, test_session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_test_tokens(monkeypatch)
    owner = _create_user(test_session_factory, "owner@example.com")
    member = _create_user(test_session_factory, "member@example.com")
    tenant = auth_client.post(
        "/api/v1/tenants", json={"name": "Company", "slug": "company"},
        headers=_authorization(owner),
    ).json()
    endpoint = f"/api/v1/tenants/{tenant['id']}/memberships"
    headers = _authorization(owner)
    added = auth_client.post(endpoint, json={"email": member.email, "role": "EMPLOYEE"}, headers=headers)
    duplicate = auth_client.post(endpoint, json={"email": member.email, "role": "HR"}, headers=headers)
    assert added.status_code == 201
    assert duplicate.status_code == 409
    changed = auth_client.patch(
        f"{endpoint}/{added.json()['id']}", json={"is_active": False}, headers=headers
    )
    assert changed.status_code == 200
    reactivated = auth_client.post(endpoint, json={"email": member.email, "role": "HR"}, headers=headers)
    assert reactivated.status_code == 201
    assert reactivated.json()["id"] == added.json()["id"]
    assert reactivated.json()["role"] == "HR"


def test_last_owner_cannot_be_deactivated_or_demoted(
    auth_client: TestClient, test_session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_test_tokens(monkeypatch)
    owner = _create_user(test_session_factory, "owner@example.com")
    tenant = auth_client.post(
        "/api/v1/tenants", json={"name": "Company", "slug": "company"},
        headers=_authorization(owner),
    ).json()
    with test_session_factory() as session:
        membership_id = str(
            session.scalar(
                select(TenantMembership.id).where(TenantMembership.tenant_id == UUID(tenant["id"]))
            )
        )
    endpoint = f"/api/v1/tenants/{tenant['id']}/memberships/{membership_id}"
    headers = _authorization(owner)
    deactivate = auth_client.patch(endpoint, json={"is_active": False}, headers=headers)
    demote = auth_client.patch(endpoint, json={"role": "ADMIN"}, headers=headers)
    assert deactivate.status_code == demote.status_code == 409, (deactivate.text, demote.text)


def test_owner_and_admin_can_update_tenant_details(
    auth_client: TestClient, test_session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_test_tokens(monkeypatch)
    owner = _create_user(test_session_factory, "owner@example.com")
    admin = _create_user(test_session_factory, "admin@example.com")
    tenant_response = auth_client.post(
        "/api/v1/tenants", json={"name": "Before", "slug": "before"},
        headers=_authorization(owner),
    )
    tenant = tenant_response.json()
    added = auth_client.post(
        f"/api/v1/tenants/{tenant['id']}/memberships",
        json={"email": admin.email, "role": "ADMIN"},
        headers=_authorization(owner),
    )
    assert added.status_code == 201

    owner_update = auth_client.patch(
        f"/api/v1/tenants/{tenant['id']}",
        json={"name": "Owner Updated", "description": "Owner description"},
        headers=_authorization(owner),
    )
    admin_update = auth_client.patch(
        f"/api/v1/tenants/{tenant['id']}",
        json={"description": None},
        headers=_authorization(admin),
    )
    assert owner_update.status_code == 200
    assert owner_update.json()["name"] == "Owner Updated"
    assert owner_update.json()["slug"] == tenant["slug"]
    assert admin_update.status_code == 200
    assert admin_update.json()["description"] is None
    assert admin_update.json()["id"] == tenant["id"]


@pytest.mark.parametrize("role", ["HR", "MANAGER", "EMPLOYEE"])
def test_non_admin_tenant_roles_cannot_update_tenant(
    auth_client: TestClient,
    test_session_factory,
    monkeypatch: pytest.MonkeyPatch,
    role: str,
) -> None:
    _enable_test_tokens(monkeypatch)
    owner = _create_user(test_session_factory, "owner@example.com")
    member = _create_user(test_session_factory, "member@example.com")
    tenant = auth_client.post(
        "/api/v1/tenants", json={"name": "Before", "slug": "before"},
        headers=_authorization(owner),
    ).json()
    added = auth_client.post(
        f"/api/v1/tenants/{tenant['id']}/memberships",
        json={"email": member.email, "role": role},
        headers=_authorization(owner),
    )
    assert added.status_code == 201
    response = auth_client.patch(
        f"/api/v1/tenants/{tenant['id']}",
        json={"name": "Unauthorized change"},
        headers=_authorization(member),
    )
    assert response.status_code == 403
    assert auth_client.get(f"/api/v1/tenants/{tenant['id']}", headers=_authorization(owner)).json()["name"] == "Before"


def test_tenant_update_hides_unrelated_tenants_and_rejects_protected_fields(
    auth_client: TestClient, test_session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_test_tokens(monkeypatch)
    owner = _create_user(test_session_factory, "owner@example.com")
    outsider = _create_user(test_session_factory, "outsider@example.com")
    tenant = auth_client.post(
        "/api/v1/tenants", json={"name": "Before", "slug": "before"},
        headers=_authorization(owner),
    ).json()
    path = f"/api/v1/tenants/{tenant['id']}"
    denied = auth_client.patch(path, json={"name": "Leak"}, headers=_authorization(outsider))
    protected = auth_client.patch(path, json={"slug": "changed"}, headers=_authorization(owner))
    empty = auth_client.patch(path, json={}, headers=_authorization(owner))
    assert denied.status_code == 404
    assert protected.status_code == empty.status_code == 422


def test_tenant_update_database_failure_returns_safe_error(
    auth_client: TestClient,
    test_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.tenants.service import TenantUpdateError

    _enable_test_tokens(monkeypatch)
    owner = _create_user(test_session_factory, "owner@example.com")
    tenant = auth_client.post(
        "/api/v1/tenants", json={"name": "Before", "slug": "before"},
        headers=_authorization(owner),
    ).json()

    def fail_update(*args, **kwargs):
        raise TenantUpdateError

    monkeypatch.setattr("app.tenants.routes.update_tenant", fail_update)
    response = auth_client.patch(
        f"/api/v1/tenants/{tenant['id']}",
        json={"description": "new description"},
        headers=_authorization(owner),
    )
    assert response.status_code == 503
    assert "database" not in response.text.casefold()


@pytest.mark.parametrize("privileged_role", [TenantRole.OWNER, TenantRole.ADMIN])
def test_admin_cannot_reactivate_inactive_privileged_membership(
    auth_client: TestClient,
    test_session_factory,
    monkeypatch: pytest.MonkeyPatch,
    privileged_role: TenantRole,
) -> None:
    _enable_test_tokens(monkeypatch)
    owner = _create_user(test_session_factory, "owner@example.com")
    admin = _create_user(test_session_factory, "admin@example.com")
    target = _create_user(test_session_factory, "target@example.com")
    tenant = auth_client.post(
        "/api/v1/tenants", json={"name": "Company", "slug": "company"},
        headers=_authorization(owner),
    ).json()
    endpoint = f"/api/v1/tenants/{tenant['id']}/memberships"
    admin_membership = auth_client.post(
        endpoint, json={"email": admin.email, "role": "ADMIN"}, headers=_authorization(owner)
    )
    target_membership = auth_client.post(
        endpoint, json={"email": target.email, "role": privileged_role.value}, headers=_authorization(owner)
    )
    assert admin_membership.status_code == target_membership.status_code == 201
    target_id = target_membership.json()["id"]
    deactivated = auth_client.patch(
        f"{endpoint}/{target_id}", json={"is_active": False}, headers=_authorization(owner)
    )
    assert deactivated.status_code == 200

    reactivation = auth_client.post(
        endpoint,
        json={"email": target.email, "role": "EMPLOYEE"},
        headers=_authorization(admin),
    )
    assert reactivation.status_code == 403
    with test_session_factory() as session:
        persisted = session.get(TenantMembership, UUID(target_id))
        assert persisted is not None
        assert persisted.is_active is False
        assert persisted.role == privileged_role
        count = session.scalar(
            select(TenantMembership).where(
                TenantMembership.tenant_id == UUID(tenant["id"]),
                TenantMembership.user_id == target.id,
            )
        )
        assert count.id == UUID(target_id)


@pytest.mark.parametrize("privileged_role", [TenantRole.OWNER, TenantRole.ADMIN])
def test_owner_can_reactivate_privileged_memberships(
    auth_client: TestClient,
    test_session_factory,
    monkeypatch: pytest.MonkeyPatch,
    privileged_role: TenantRole,
) -> None:
    _enable_test_tokens(monkeypatch)
    owner = _create_user(test_session_factory, "owner@example.com")
    target = _create_user(test_session_factory, "target@example.com")
    tenant = auth_client.post(
        "/api/v1/tenants", json={"name": "Company", "slug": "company"},
        headers=_authorization(owner),
    ).json()
    endpoint = f"/api/v1/tenants/{tenant['id']}/memberships"
    created = auth_client.post(
        endpoint,
        json={"email": target.email, "role": privileged_role.value},
        headers=_authorization(owner),
    )
    assert created.status_code == 201
    membership_id = created.json()["id"]
    assert auth_client.patch(
        f"{endpoint}/{membership_id}", json={"is_active": False}, headers=_authorization(owner)
    ).status_code == 200
    reactivated = auth_client.post(
        endpoint,
        json={"email": target.email, "role": privileged_role.value},
        headers=_authorization(owner),
    )
    assert reactivated.status_code == 201
    assert reactivated.json()["id"] == membership_id
    assert reactivated.json()["role"] == privileged_role.value


def test_db_partial_unique_index_blocks_duplicate_active_memberships(test_session_factory) -> None:
    with test_session_factory() as session:
        user = User(email="index@example.com", hashed_password="hash", full_name="Index User")
        tenant = Tenant(name="Index tenant", slug="index-tenant")
        session.add_all([user, tenant])
        session.flush()
        session.add(TenantMembership(tenant_id=tenant.id, user_id=user.id, role=TenantRole.EMPLOYEE))
        session.commit()
        session.add(TenantMembership(tenant_id=tenant.id, user_id=user.id, role=TenantRole.HR))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_inactive_duplicate_membership_can_be_reactivated(test_session_factory) -> None:
    with test_session_factory() as session:
        user = User(email="reactivate@example.com", hashed_password="hash", full_name="Reactivate")
        tenant = Tenant(name="Reactivate tenant", slug="reactivate-tenant")
        session.add_all([user, tenant])
        session.flush()
        session.add(TenantMembership(
            tenant_id=tenant.id, user_id=user.id, role=TenantRole.EMPLOYEE, is_active=False
        ))
        session.add(TenantMembership(
            tenant_id=tenant.id, user_id=user.id, role=TenantRole.HR, is_active=True
        ))
        session.commit()
        assert len(session.scalars(select(TenantMembership)).all()) == 2


def test_concurrent_membership_inserts_cannot_create_duplicates(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'membership-race.db'}", connect_args={"timeout": 10})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        with factory() as session:
            user = User(email="race@example.com", hashed_password="hash", full_name="Race")
            tenant = Tenant(name="Race Company", slug="race-company")
            session.add_all([user, tenant])
            session.commit()
            tenant_id, user_id = tenant.id, user.id

        barrier = Barrier(2)

        def insert_membership(role: TenantRole) -> bool:
            with factory() as session:
                barrier.wait(timeout=5)
                session.add(TenantMembership(tenant_id=tenant_id, user_id=user_id, role=role))
                try:
                    session.commit()
                    return True
                except SQLAlchemyError:
                    session.rollback()
                    return False

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(insert_membership, [TenantRole.EMPLOYEE, TenantRole.HR]))
        with factory() as session:
            active_count = session.scalar(
                select(func.count(TenantMembership.id)).where(
                    TenantMembership.tenant_id == tenant_id,
                    TenantMembership.user_id == user_id,
                    TenantMembership.is_active.is_(True),
                )
            )
        assert sum(outcomes) == 1
        assert active_count == 1
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_tenant_memberships_are_separate_and_restrict_delete_relationships() -> None:
    assert "tenant_id" not in User.__table__.columns
    assert "tenants" in Base.metadata.tables
    assert "tenant_memberships" in Base.metadata.tables
    membership_table = TenantMembership.__table__
    foreign_keys = {foreign_key.parent.name: foreign_key for foreign_key in membership_table.foreign_keys}
    assert foreign_keys["tenant_id"].target_fullname == "tenants.id"
    assert foreign_keys["user_id"].target_fullname == "users.id"
    assert foreign_keys["tenant_id"].ondelete == "RESTRICT"
    assert foreign_keys["user_id"].ondelete == "RESTRICT"
