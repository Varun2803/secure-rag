from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.tenants.models import Tenant, TenantMembership, TenantRole
from app.tenants.repository import get_any_membership, get_membership_by_id
from app.users.models import User
from app.users.repository import get_user_by_email


class DuplicateTenantSlugError(ValueError):
    """Raised when a tenant slug is already in use."""


class DuplicateMembershipError(ValueError):
    """Raised when a user already has an active membership in the tenant."""


class MembershipUserUnavailableError(ValueError):
    """Raised when the requested account cannot join a tenant."""


class MembershipNotFoundError(ValueError):
    """Raised when a membership does not belong to the requested tenant."""


class LastOwnerError(ValueError):
    """Raised when an operation would leave a tenant without an active owner."""


class SelfRemovalError(ValueError):
    """Raised when an actor tries to deactivate their own membership."""


class TenantPermissionError(ValueError):
    """Raised when tenant role permissions do not allow an operation."""


class TenantUpdateError(RuntimeError):
    """Raised when tenant changes cannot be committed."""


def _lock_tenant_admin(
    session: Session, *, tenant_id: UUID, actor_user_id: UUID
) -> TenantMembership:
    tenant = session.scalar(
        select(Tenant)
        .where(Tenant.id == tenant_id, Tenant.is_active.is_(True))
        .with_for_update()
    )
    if tenant is None:
        raise MembershipNotFoundError
    actor_membership = session.scalar(
        select(TenantMembership)
        .where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.user_id == actor_user_id,
            TenantMembership.is_active.is_(True),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if actor_membership is None or actor_membership.role not in {TenantRole.OWNER, TenantRole.ADMIN}:
        raise TenantPermissionError
    return actor_membership


def create_tenant(
    session: Session, *, name: str, slug: str, description: str | None, owner: User
) -> Tenant:
    tenant = Tenant(name=name.strip(), slug=Tenant.normalize_slug(slug), description=description)
    try:
        session.add(tenant)
        session.flush()
        session.add(TenantMembership(tenant=tenant, user=owner, role=TenantRole.OWNER))
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise DuplicateTenantSlugError from exc
    session.refresh(tenant)
    return tenant


def update_tenant(
    session: Session,
    *,
    tenant_id: UUID,
    actor_user_id: UUID,
    name: str | None,
    description: str | None,
    update_description: bool,
) -> Tenant:
    try:
        _lock_tenant_admin(session, tenant_id=tenant_id, actor_user_id=actor_user_id)
        tenant = session.scalar(
            select(Tenant)
            .where(Tenant.id == tenant_id, Tenant.is_active.is_(True))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if tenant is None:
            raise MembershipNotFoundError
        if name is not None:
            tenant.name = name
        if update_description:
            tenant.description = description
        session.flush()
        session.commit()
        session.refresh(tenant)
    except SQLAlchemyError as exc:
        session.rollback()
        raise TenantUpdateError from exc
    return tenant


def add_membership(
    session: Session, *, tenant_id: UUID, actor_user_id: UUID, email: str, role: TenantRole
) -> TenantMembership:
    actor_membership = _lock_tenant_admin(session, tenant_id=tenant_id, actor_user_id=actor_user_id)
    if role in {TenantRole.OWNER, TenantRole.ADMIN} and actor_membership.role != TenantRole.OWNER:
        raise TenantPermissionError
    user = get_user_by_email(session, email)
    if user is None or not user.is_active:
        raise MembershipUserUnavailableError
    membership = get_any_membership(session, tenant_id=tenant_id, user_id=user.id)
    if membership is not None and membership.is_active:
        raise DuplicateMembershipError
    if (
        membership is not None
        and actor_membership.role == TenantRole.ADMIN
        and membership.role in {TenantRole.OWNER, TenantRole.ADMIN}
    ):
        raise TenantPermissionError
    if membership is None:
        membership = TenantMembership(tenant_id=tenant_id, user_id=user.id, role=role)
        session.add(membership)
    else:
        membership.role = role
        membership.is_active = True
        membership.joined_at = datetime.now(timezone.utc)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise DuplicateMembershipError from exc
    session.refresh(membership)
    return membership


def update_membership(
    session: Session,
    *,
    tenant_id: UUID,
    membership_id: UUID,
    actor_user_id: UUID,
    role: TenantRole | None,
    is_active: bool | None,
) -> TenantMembership:
    # Serialize owner changes on PostgreSQL so concurrent requests cannot remove all owners.
    actor_membership = _lock_tenant_admin(session, tenant_id=tenant_id, actor_user_id=actor_user_id)
    membership = session.scalar(
        select(TenantMembership)
        .where(TenantMembership.tenant_id == tenant_id, TenantMembership.id == membership_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if membership is None:
        raise MembershipNotFoundError
    if actor_membership.role == TenantRole.ADMIN and (
        membership.role in {TenantRole.OWNER, TenantRole.ADMIN}
        or role in {TenantRole.OWNER, TenantRole.ADMIN}
    ):
        raise TenantPermissionError
    if is_active is False and membership.user_id == actor_user_id:
        raise SelfRemovalError
    if role is not None:
        membership.role = role
    if is_active is not None:
        membership.is_active = is_active
    # The shared session factory disables autoflush, so apply pending membership
    # changes before checking the active-owner invariant.
    session.flush()
    owner_count = session.scalar(
        select(func.count(TenantMembership.id)).where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.is_active.is_(True),
            TenantMembership.role == TenantRole.OWNER,
        )
    ) or 0
    if owner_count == 0:
        session.rollback()
        raise LastOwnerError
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise DuplicateMembershipError from exc
    session.refresh(membership)
    return membership
