from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.tenants.models import TenantMembership
from app.users.models import User


class PermissionAssignmentError(ValueError):
    """One or more allowed users are not active members of this tenant."""


class PermissionValidationUnavailable(RuntimeError):
    """The requested assignments could not be checked safely."""


def validate_allowed_users(session: Session, tenant_id: UUID, user_ids: list[UUID]) -> list[str]:
    """Return normalized IDs after validating active users and same-tenant membership."""
    if len(set(user_ids)) != len(user_ids):
        raise PermissionAssignmentError("Duplicate document user assignments")
    if not user_ids:
        return []
    try:
        eligible = set(session.scalars(
            select(User.id)
            .join(TenantMembership, TenantMembership.user_id == User.id)
            .where(
                User.id.in_(user_ids),
                User.is_active.is_(True),
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.is_active.is_(True),
            )
        ).all())
    except SQLAlchemyError as exc:
        raise PermissionValidationUnavailable from exc
    if eligible != set(user_ids):
        raise PermissionAssignmentError("Invalid document user assignments")
    return [str(user_id) for user_id in user_ids]
