from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_active_user
from app.audit.dependencies import record_request_event
from app.audit.events import AuditEventType, AuditOutcome
from app.database.postgres import get_db
from app.tenants.models import TenantMembership, TenantRole
from app.tenants.repository import get_active_membership
from app.users.models import User

SessionDependency = Annotated[Session, Depends(get_db)]
UserDependency = Annotated[User, Depends(get_current_active_user)]


@dataclass(frozen=True, slots=True)
class TenantContext:
    user_id: UUID
    tenant_id: UUID
    membership_id: UUID
    role: TenantRole


def get_current_tenant_membership(
    tenant_id: UUID, session: SessionDependency, user: UserDependency, request: Request,
) -> TenantMembership:
    membership = get_active_membership(session, tenant_id=tenant_id, user_id=user.id)
    if membership is None:
        record_request_event(
            request, session, event_type=AuditEventType.TENANT_ACCESS_DENIED,
            action="tenant.access", outcome=AuditOutcome.DENIED,
            actor_user_id=user.id, resource_type="tenant", resource_id=tenant_id,
            reason_code="ACTIVE_MEMBERSHIP_REQUIRED",
        )
        if "/retrieval/" in request.url.path:
            record_request_event(
                request, session, event_type=AuditEventType.RETRIEVAL_ACCESS_DENIED,
                action="retrieval.authorize", outcome=AuditOutcome.DENIED,
                actor_user_id=user.id, resource_type="tenant", resource_id=tenant_id,
                reason_code="ACTIVE_MEMBERSHIP_REQUIRED",
            )
        elif "/generation/" in request.url.path:
            record_request_event(
                request, session, event_type=AuditEventType.GENERATION_ACCESS_DENIED,
                action="generation.authorize", outcome=AuditOutcome.DENIED,
                actor_user_id=user.id, resource_type="tenant", resource_id=tenant_id,
                reason_code="ACTIVE_MEMBERSHIP_REQUIRED",
            )
        # Do not disclose whether another tenant exists.
        raise HTTPException(status_code=404, detail="Tenant not found")
    record_request_event(
        request, session, event_type=AuditEventType.TENANT_ACCESS_SUCCESS,
        action="tenant.access", outcome=AuditOutcome.SUCCESS,
        tenant_id=membership.tenant_id, actor_user_id=user.id,
        resource_type="tenant", resource_id=membership.tenant_id,
    )
    return membership


def get_current_tenant_context(
    membership: Annotated[TenantMembership, Depends(get_current_tenant_membership)],
) -> TenantContext:
    """Build trusted tenant context only from verified user and membership records."""
    return TenantContext(
        user_id=membership.user_id,
        tenant_id=membership.tenant_id,
        membership_id=membership.id,
        role=membership.role,
    )


TenantContextDependency = Annotated[TenantContext, Depends(get_current_tenant_context)]


def require_tenant_roles(*allowed_roles: TenantRole):
    def dependency(
        membership: Annotated[TenantMembership, Depends(get_current_tenant_membership)],
        session: SessionDependency,
        request: Request,
    ) -> TenantMembership:
        if membership.role not in allowed_roles:
            record_request_event(
                request, session, event_type=AuditEventType.TENANT_ACCESS_DENIED,
                action="tenant.role.authorize", outcome=AuditOutcome.DENIED,
                tenant_id=membership.tenant_id, actor_user_id=membership.user_id,
                resource_type="tenant", resource_id=membership.tenant_id,
                reason_code="INSUFFICIENT_ROLE",
                metadata={"membership_role": membership.role.value},
            )
            raise HTTPException(status_code=403, detail="Insufficient tenant privileges")
        return membership

    return dependency
