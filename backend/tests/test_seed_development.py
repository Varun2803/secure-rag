import pytest
from datetime import datetime
from sqlalchemy import func, select
from pydantic import SecretStr

from app.auth.security import hash_password, verify_password
from app.config import Settings
from app.tenants.models import Tenant, TenantMembership, TenantRole
from app.users.models import User
from app.seed_development import main, seed_development

_DEVELOPMENT_PASSWORD = "OnlyForLocalDev123!"


def test_initial_development_seed_creates_expected_users_tenants_and_memberships(
    test_session_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.seed_development.get_settings", lambda: Settings(app_env="development"))
    with test_session_factory() as session:
        result = seed_development(session, password=_DEVELOPMENT_PASSWORD)
        assert result.users_created == 8
        assert result.tenants_created == 3
        assert result.memberships_created == 8
        users = list(session.scalars(select(User)).all())
        tenants = list(session.scalars(select(Tenant)).all())
        memberships = list(session.scalars(select(TenantMembership)).all())
        assert len(users) == 8
        assert len(tenants) == 3
        assert len(memberships) == 8
        alice = session.scalar(select(User).where(User.email == "alice@technova.local"))
        assert verify_password(_DEVELOPMENT_PASSWORD, alice.hashed_password)
        assert alice.hashed_password != _DEVELOPMENT_PASSWORD
        roles = {
            (membership.tenant.slug, membership.user.email): membership.role
            for membership in memberships
        }
        assert roles[("technova-solutions", "alice@technova.local")] == TenantRole.OWNER
        assert roles[("technova-solutions", "bob@technova.local")] == TenantRole.HR
        assert roles[("technova-solutions", "charlie@technova.local")] == TenantRole.EMPLOYEE
        assert roles[("finedge-corporation", "david@finedge.local")] == TenantRole.OWNER
        assert roles[("finedge-corporation", "emma@finedge.local")] == TenantRole.MANAGER
        assert roles[("finedge-corporation", "frank@finedge.local")] == TenantRole.EMPLOYEE
        assert roles[("healthbridge-systems", "george@healthbridge.local")] == TenantRole.OWNER
        assert roles[("healthbridge-systems", "harry@healthbridge.local")] == TenantRole.EMPLOYEE


def test_development_seed_is_idempotent_and_prevents_duplicates(
    test_session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.seed_development.get_settings", lambda: Settings(app_env="development"))
    with test_session_factory() as session:
        seed_development(session, password=_DEVELOPMENT_PASSWORD)
        result = seed_development(session, password="DifferentLocalDev123!")
        assert (result.users_created, result.tenants_created, result.memberships_created) == (0, 0, 0)
        assert session.scalar(select(func.count(User.id))) == 8
        assert session.scalar(select(func.count(Tenant.id))) == 3
        assert session.scalar(select(func.count(TenantMembership.id))) == 8


def test_existing_inactive_user_tenant_and_membership_are_not_modified(
    test_session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.seed_development.get_settings", lambda: Settings(app_env="development"))
    with test_session_factory() as session:
        seed_development(session, password=_DEVELOPMENT_PASSWORD)
        user = session.scalar(select(User).where(User.email == "alice@technova.local"))
        tenant = session.scalar(select(Tenant).where(Tenant.slug == "technova-solutions"))
        membership = session.scalar(
            select(TenantMembership).where(
                TenantMembership.tenant_id == tenant.id,
                TenantMembership.user_id == user.id,
            )
        )
        user.is_active = False
        user.full_name = "Existing Alice"
        user.hashed_password = hash_password("ExistingAccountPassword123!")
        tenant.is_active = False
        tenant.name = "Existing TechNova Name"
        tenant.description = "Existing tenant description"
        membership.is_active = False
        membership.role = TenantRole.ADMIN
        membership.joined_at = datetime(2020, 1, 2, 3, 4, 5)
        session.commit()
        original_password_hash = user.hashed_password
        original_joined_at = membership.joined_at
        original_created_at = membership.created_at

        result = seed_development(session, password="DifferentSeedPassword123!")
        assert (result.users_created, result.tenants_created, result.memberships_created) == (0, 0, 0)
        session.refresh(user)
        session.refresh(tenant)
        session.refresh(membership)
        assert user.is_active is False
        assert user.email == "alice@technova.local"
        assert user.full_name == "Existing Alice"
        assert user.hashed_password == original_password_hash
        assert verify_password("ExistingAccountPassword123!", user.hashed_password)
        assert tenant.is_active is False
        assert tenant.name == "Existing TechNova Name"
        assert tenant.slug == "technova-solutions"
        assert tenant.description == "Existing tenant description"
        assert membership.is_active is False
        assert membership.role == TenantRole.ADMIN
        assert membership.joined_at == original_joined_at
        assert membership.created_at == original_created_at


@pytest.mark.parametrize(
    ("email", "existing_role"),
    [
        ("bob@technova.local", TenantRole.OWNER),
        ("charlie@technova.local", TenantRole.ADMIN),
    ],
)
def test_existing_owner_and_admin_roles_are_not_overwritten(
    test_session_factory,
    monkeypatch: pytest.MonkeyPatch,
    email: str,
    existing_role: TenantRole,
) -> None:
    monkeypatch.setattr("app.seed_development.get_settings", lambda: Settings(app_env="development"))
    with test_session_factory() as session:
        seed_development(session, password=_DEVELOPMENT_PASSWORD)
        membership = session.scalar(
            select(TenantMembership)
            .join(User)
            .where(User.email == email)
        )
        membership.role = existing_role
        session.commit()
        membership_id = membership.id

        seed_development(session, password="AnotherSeedPassword123!")

        refreshed = session.get(TenantMembership, membership_id)
        assert refreshed.role == existing_role


def test_missing_records_are_created_only_for_active_eligible_pairs(
    test_session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.seed_development.get_settings", lambda: Settings(app_env="development"))
    with test_session_factory() as session:
        existing_user = User(
            email="alice@technova.local",
            hashed_password=hash_password("ExistingInactivePassword123!"),
            full_name="Existing Alice",
            is_active=False,
        )
        existing_tenant = Tenant(
            name="Existing inactive tenant",
            slug="technova-solutions",
            description="Do not change",
            is_active=False,
        )
        session.add_all([existing_user, existing_tenant])
        session.flush()
        existing_membership = TenantMembership(
            tenant_id=existing_tenant.id,
            user_id=existing_user.id,
            role=TenantRole.ADMIN,
            is_active=False,
            joined_at=datetime(2020, 1, 1),
        )
        session.add(existing_membership)
        session.commit()
        user_id = existing_user.id
        tenant_id = existing_tenant.id
        membership_id = existing_membership.id
        saved_hash = existing_user.hashed_password
        saved_joined_at = existing_membership.joined_at

        result = seed_development(session, password=_DEVELOPMENT_PASSWORD)
        assert (result.users_created, result.tenants_created, result.memberships_created) == (7, 2, 5)
        assert session.scalar(select(func.count(User.id))) == 8
        assert session.scalar(select(func.count(Tenant.id))) == 3
        assert session.scalar(select(func.count(TenantMembership.id))) == 6
        inactive_user = session.get(User, user_id)
        inactive_tenant = session.get(Tenant, tenant_id)
        inactive_membership = session.get(TenantMembership, membership_id)
        assert inactive_user.is_active is False
        assert inactive_user.hashed_password == saved_hash
        assert inactive_tenant.is_active is False
        assert inactive_tenant.name == "Existing inactive tenant"
        assert inactive_membership.is_active is False
        assert inactive_membership.role == TenantRole.ADMIN
        assert inactive_membership.joined_at == saved_joined_at

        for tenant_slug, skipped_emails in (
            ("technova-solutions", {"bob@technova.local", "charlie@technova.local"}),
        ):
            tenant = session.scalar(select(Tenant).where(Tenant.slug == tenant_slug))
            for email in skipped_emails:
                user = session.scalar(select(User).where(User.email == email))
                membership = session.scalar(
                    select(TenantMembership).where(
                        TenantMembership.tenant_id == tenant.id,
                        TenantMembership.user_id == user.id,
                    )
                )
                assert membership is None

        created_user = session.scalar(select(User).where(User.email == "david@finedge.local"))
        assert created_user is not None
        assert created_user.hashed_password.startswith("$argon2id$")


def test_seed_main_refuses_production_before_opening_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.seed_development.get_settings", lambda: Settings(
        app_env="production", secret_key="0123456789abcdef0123456789abcdef",
        trusted_hosts="api.example.com", database_url="postgresql+psycopg://user:password@db.example.com/securerag",
        qdrant_url="https://qdrant.example.com", qdrant_api_key="qdrant-test-api-key",
    ))

    def should_not_connect():
        raise AssertionError("production seed attempted to open a database session")

    monkeypatch.setattr("app.seed_development.get_session_factory", should_not_connect)
    assert main() == 2


def test_seed_operation_itself_refuses_production(
    test_session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.seed_development import ProductionSeedRefused

    monkeypatch.setattr("app.seed_development.get_settings", lambda: Settings(
        app_env="production", secret_key="0123456789abcdef0123456789abcdef",
        trusted_hosts="api.example.com", database_url="postgresql+psycopg://user:password@db.example.com/securerag",
        qdrant_url="https://qdrant.example.com", qdrant_api_key="qdrant-test-api-key",
    ))
    with test_session_factory() as session:
        with pytest.raises(ProductionSeedRefused):
            seed_development(session, password=_DEVELOPMENT_PASSWORD)
