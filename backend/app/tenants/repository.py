from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.tenants.models import Tenant, TenantMembership


def get_tenant_by_id(session: Session, tenant_id: UUID) -> Tenant | None:
    return session.get(Tenant, tenant_id)


def get_tenant_by_slug(session: Session, slug: str) -> Tenant | None:
    return session.scalar(select(Tenant).where(Tenant.slug == Tenant.normalize_slug(slug)))


def list_tenants_for_user(session: Session, user_id: UUID) -> list[TenantMembership]:
    statement = (
        select(TenantMembership)
        .options(joinedload(TenantMembership.tenant))
        .where(
            TenantMembership.user_id == user_id,
            TenantMembership.is_active.is_(True),
            Tenant.is_active.is_(True),
        )
        .join(TenantMembership.tenant)
        .order_by(Tenant.name)
    )
    return list(session.scalars(statement).unique().all())


def get_active_membership(
    session: Session, *, tenant_id: UUID, user_id: UUID
) -> TenantMembership | None:
    statement = (
        select(TenantMembership)
        .options(joinedload(TenantMembership.tenant))
        .join(TenantMembership.tenant)
        .where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.user_id == user_id,
            TenantMembership.is_active.is_(True),
            Tenant.is_active.is_(True),
        )
    )
    return session.scalar(statement)


def get_membership_by_id(
    session: Session, *, tenant_id: UUID, membership_id: UUID
) -> TenantMembership | None:
    return session.scalar(
        select(TenantMembership)
        .options(joinedload(TenantMembership.user))
        .where(TenantMembership.tenant_id == tenant_id, TenantMembership.id == membership_id)
    )


def get_any_membership(
    session: Session, *, tenant_id: UUID, user_id: UUID
) -> TenantMembership | None:
    return session.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.user_id == user_id,
        ).order_by(TenantMembership.is_active.desc(), TenantMembership.created_at.desc())
    )


def list_memberships(session: Session, tenant_id: UUID) -> list[TenantMembership]:
    statement = (
        select(TenantMembership)
        .options(joinedload(TenantMembership.user))
        .where(TenantMembership.tenant_id == tenant_id)
        .order_by(TenantMembership.created_at, TenantMembership.id)
    )
    return list(session.scalars(statement).unique().all())
