"""Explicit, development-only seed data for local testing."""

import os
import sys
from dataclasses import dataclass

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth.security import hash_password, validate_password
from app.config import get_settings
from app.database.postgres import get_session_factory
from app.tenants.models import Tenant, TenantMembership, TenantRole
from app.tenants.repository import get_any_membership, get_tenant_by_slug
from app.users.models import User
from app.users.repository import get_user_by_email


@dataclass(frozen=True, slots=True)
class SeedUser:
    name: str
    email: str
    role: TenantRole


@dataclass(frozen=True, slots=True)
class SeedTenant:
    name: str
    slug: str
    description: str
    users: tuple[SeedUser, ...]


SEED_TENANTS = (
    SeedTenant(
        "TechNova Solutions",
        "technova-solutions",
        "Fictional technology company for local development.",
        (
            SeedUser("Alice", "alice@technova.local", TenantRole.OWNER),
            SeedUser("Bob", "bob@technova.local", TenantRole.HR),
            SeedUser("Charlie", "charlie@technova.local", TenantRole.EMPLOYEE),
        ),
    ),
    SeedTenant(
        "FinEdge Corporation",
        "finedge-corporation",
        "Fictional financial services company for local development.",
        (
            SeedUser("David", "david@finedge.local", TenantRole.OWNER),
            SeedUser("Emma", "emma@finedge.local", TenantRole.MANAGER),
            SeedUser("Frank", "frank@finedge.local", TenantRole.EMPLOYEE),
        ),
    ),
    SeedTenant(
        "HealthBridge Systems",
        "healthbridge-systems",
        "Fictional healthcare technology company for local development.",
        (
            SeedUser("George", "george@healthbridge.local", TenantRole.OWNER),
            SeedUser("Harry", "harry@healthbridge.local", TenantRole.EMPLOYEE),
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class SeedResult:
    users_created: int
    tenants_created: int
    memberships_created: int


class ProductionSeedRefused(RuntimeError):
    """Raised if development seed data is requested in production."""


def seed_development(session: Session, *, password: str) -> SeedResult:
    """Create missing fictional development records without changing existing ones."""
    if get_settings().app_env.strip().casefold() == "production":
        raise ProductionSeedRefused("Development seed refused when APP_ENV is production")
    validate_password(password)
    user_count = tenant_count = membership_count = 0
    try:
        for tenant_seed in SEED_TENANTS:
            tenant = get_tenant_by_slug(session, tenant_seed.slug)
            if tenant is None:
                tenant = Tenant(
                    name=tenant_seed.name,
                    slug=tenant_seed.slug,
                    description=tenant_seed.description,
                )
                session.add(tenant)
                session.flush()
                tenant_count += 1

            for user_seed in tenant_seed.users:
                user = get_user_by_email(session, user_seed.email)
                if user is None:
                    user = User(
                        email=User.normalize_email(user_seed.email),
                        hashed_password=hash_password(password),
                        full_name=user_seed.name,
                    )
                    session.add(user)
                    session.flush()
                    user_count += 1

                membership = get_any_membership(session, tenant_id=tenant.id, user_id=user.id)
                if membership is None and user.is_active and tenant.is_active:
                    session.add(
                        TenantMembership(tenant_id=tenant.id, user_id=user.id, role=user_seed.role)
                    )
                    membership_count += 1
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        raise
    return SeedResult(user_count, tenant_count, membership_count)


def main() -> int:
    if get_settings().app_env.strip().casefold() == "production":
        print("Development seed refused when APP_ENV is production.", file=sys.stderr)
        return 2
    password = os.environ.get("DEV_SEED_PASSWORD")
    if not password:
        print("Set DEV_SEED_PASSWORD to a development-only password (12–128 characters).", file=sys.stderr)
        return 2
    with get_session_factory()() as session:
        result = seed_development(session, password=password)
    print(
        "Development seed complete: "
        f"{result.users_created} users, {result.tenants_created} tenants, "
        f"{result.memberships_created} memberships created."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
