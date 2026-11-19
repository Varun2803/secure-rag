from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_active_user
from app.audit.dependencies import record_request_event
from app.audit.events import AuditEventType, AuditOutcome
from app.database.postgres import get_db
from app.tenants.dependencies import (
    TenantContextDependency,
    get_current_tenant_membership,
    require_tenant_roles,
)
from app.tenants.models import Tenant, TenantMembership, TenantRole
from app.tenants.repository import (
    list_memberships,
    list_tenants_for_user,
)
from app.tenants.schemas import (
    TenantCreate,
    TenantListItem,
    TenantMembershipCreate,
    TenantMembershipPublic,
    TenantMembershipUpdate,
    TenantPublic,
    TenantUpdate,
)
from app.tenants.service import (
    DuplicateMembershipError,
    DuplicateTenantSlugError,
    LastOwnerError,
    MembershipNotFoundError,
    MembershipUserUnavailableError,
    SelfRemovalError,
    TenantPermissionError,
    TenantUpdateError,
    add_membership,
    create_tenant,
    update_membership,
    update_tenant,
)
from app.users.models import User

router = APIRouter(prefix="/tenants", tags=["tenants"])
SessionDependency = Annotated[Session, Depends(get_db)]
UserDependency = Annotated[User, Depends(get_current_active_user)]
TenantMembershipDependency = Annotated[TenantMembership, Depends(get_current_tenant_membership)]
TenantAdminDependency = Annotated[
    TenantMembership,
    Depends(require_tenant_roles(TenantRole.OWNER, TenantRole.ADMIN)),
]


@router.post("", response_model=TenantPublic, status_code=status.HTTP_201_CREATED)
def create_company(payload: TenantCreate, session: SessionDependency, user: UserDependency, request: Request) -> Tenant:
    try:
        tenant = create_tenant(
            session,
            name=payload.name,
            slug=payload.slug,
            description=payload.description,
            owner=user,
        )
        record_request_event(
            request, session, event_type=AuditEventType.TENANT_CREATED,
            action="tenant.create", outcome=AuditOutcome.SUCCESS,
            tenant_id=tenant.id, actor_user_id=user.id, resource_type="tenant", resource_id=tenant.id,
        )
        return tenant
    except DuplicateTenantSlugError as exc:
        record_request_event(
            request, session, event_type=AuditEventType.TENANT_ADMIN_ACTION_FAILURE,
            action="tenant.create", outcome=AuditOutcome.FAILURE,
            actor_user_id=user.id, resource_type="tenant", reason_code="DUPLICATE_TENANT",
        )
        raise HTTPException(status_code=409, detail="A tenant with this slug already exists") from exc


@router.get("", response_model=list[TenantListItem])
def list_my_tenants(session: SessionDependency, user: UserDependency) -> list[TenantListItem]:
    memberships = list_tenants_for_user(session, user.id)
    return [TenantListItem(tenant=item.tenant, role=item.role, joined_at=item.joined_at) for item in memberships]


@router.get("/{tenant_id}", response_model=TenantPublic)
def read_tenant(membership: TenantMembershipDependency) -> Tenant:
    return membership.tenant


@router.patch("/{tenant_id}", response_model=TenantPublic)
def update_company(
    tenant_id: UUID,
    payload: TenantUpdate,
    session: SessionDependency,
    context: TenantContextDependency,
    request: Request,
) -> Tenant:
    if context.role not in {TenantRole.OWNER, TenantRole.ADMIN}:
        record_request_event(
            request, session, event_type=AuditEventType.TENANT_ACCESS_DENIED,
            action="tenant.update", outcome=AuditOutcome.DENIED,
            tenant_id=context.tenant_id, actor_user_id=context.user_id,
            resource_type="tenant", resource_id=tenant_id, reason_code="INSUFFICIENT_ROLE",
        )
        raise HTTPException(status_code=403, detail="Insufficient tenant privileges")
    try:
        tenant = update_tenant(
            session,
            tenant_id=tenant_id,
            actor_user_id=context.user_id,
            name=payload.name,
            description=payload.description,
            update_description="description" in payload.model_fields_set,
        )
        record_request_event(
            request, session, event_type=AuditEventType.TENANT_UPDATED,
            action="tenant.update", outcome=AuditOutcome.SUCCESS,
            tenant_id=tenant.id, actor_user_id=context.user_id,
            resource_type="tenant", resource_id=tenant.id,
            metadata={"changed_fields": sorted(payload.model_fields_set)},
        )
        return tenant
    except MembershipNotFoundError as exc:
        record_request_event(
            request, session, event_type=AuditEventType.TENANT_ACCESS_DENIED,
            action="tenant.update", outcome=AuditOutcome.DENIED,
            tenant_id=context.tenant_id, actor_user_id=context.user_id,
            reason_code="TENANT_UNAVAILABLE",
        )
        raise HTTPException(status_code=404, detail="Tenant not found") from exc
    except TenantPermissionError as exc:
        record_request_event(
            request, session, event_type=AuditEventType.TENANT_ACCESS_DENIED,
            action="tenant.update", outcome=AuditOutcome.DENIED,
            tenant_id=context.tenant_id, actor_user_id=context.user_id,
            reason_code="INSUFFICIENT_ROLE",
        )
        raise HTTPException(status_code=403, detail="Insufficient tenant privileges") from exc
    except TenantUpdateError as exc:
        session.rollback()
        record_request_event(
            request, session, event_type=AuditEventType.TENANT_ADMIN_ACTION_FAILURE,
            action="tenant.update", outcome=AuditOutcome.FAILURE,
            tenant_id=context.tenant_id, actor_user_id=context.user_id,
            reason_code="DATABASE_FAILURE",
        )
        raise HTTPException(status_code=503, detail="Tenant update could not be completed") from exc


@router.get("/{tenant_id}/memberships", response_model=list[TenantMembershipPublic])
def list_tenant_memberships(
    tenant_id: UUID, session: SessionDependency, _: TenantAdminDependency
) -> list[TenantMembership]:
    return list_memberships(session, tenant_id)


@router.post(
    "/{tenant_id}/memberships",
    response_model=TenantMembershipPublic,
    status_code=status.HTTP_201_CREATED,
)
def create_tenant_membership(
    tenant_id: UUID,
    payload: TenantMembershipCreate,
    session: SessionDependency,
    actor_membership: TenantAdminDependency,
    request: Request,
) -> TenantMembership:
    try:
        membership = add_membership(
            session,
            tenant_id=tenant_id,
            actor_user_id=actor_membership.user_id,
            email=str(payload.email),
            role=payload.role,
        )
        record_request_event(
            request, session, event_type=AuditEventType.TENANT_MEMBERSHIP_CHANGED,
            action="tenant.membership.create", outcome=AuditOutcome.SUCCESS,
            tenant_id=membership.tenant_id, actor_user_id=actor_membership.user_id,
            resource_type="tenant_membership", resource_id=membership.id,
            metadata={"target_role": membership.role.value, "membership_active": membership.is_active},
        )
        return membership
    except DuplicateMembershipError as exc:
        record_request_event(request, session, event_type=AuditEventType.TENANT_ADMIN_ACTION_FAILURE,
                             action="tenant.membership.create", outcome=AuditOutcome.FAILURE,
                             tenant_id=actor_membership.tenant_id, actor_user_id=actor_membership.user_id,
                             reason_code="MEMBERSHIP_EXISTS")
        raise HTTPException(status_code=409, detail="User already has an active tenant membership") from exc
    except MembershipUserUnavailableError as exc:
        record_request_event(request, session, event_type=AuditEventType.TENANT_ADMIN_ACTION_FAILURE,
                             action="tenant.membership.create", outcome=AuditOutcome.FAILURE,
                             tenant_id=actor_membership.tenant_id, actor_user_id=actor_membership.user_id,
                             reason_code="USER_UNAVAILABLE")
        raise HTTPException(status_code=404, detail="User not found or inactive") from exc
    except MembershipNotFoundError as exc:
        record_request_event(request, session, event_type=AuditEventType.TENANT_ACCESS_DENIED,
                             action="tenant.membership.create", outcome=AuditOutcome.DENIED,
                             tenant_id=actor_membership.tenant_id, actor_user_id=actor_membership.user_id,
                             reason_code="TENANT_UNAVAILABLE")
        raise HTTPException(status_code=404, detail="Tenant not found") from exc
    except TenantPermissionError as exc:
        record_request_event(request, session, event_type=AuditEventType.TENANT_ACCESS_DENIED,
                             action="tenant.membership.create", outcome=AuditOutcome.DENIED,
                             tenant_id=actor_membership.tenant_id, actor_user_id=actor_membership.user_id,
                             reason_code="ROLE_NOT_ALLOWED")
        raise HTTPException(status_code=403, detail="Insufficient tenant privileges") from exc


@router.patch("/{tenant_id}/memberships/{membership_id}", response_model=TenantMembershipPublic)
def change_tenant_membership(
    tenant_id: UUID,
    membership_id: UUID,
    payload: TenantMembershipUpdate,
    session: SessionDependency,
    actor_membership: TenantAdminDependency,
    request: Request,
) -> TenantMembership:
    try:
        membership = update_membership(
            session,
            tenant_id=tenant_id,
            membership_id=membership_id,
            actor_user_id=actor_membership.user_id,
            role=payload.role,
            is_active=payload.is_active,
        )
        record_request_event(
            request, session, event_type=AuditEventType.TENANT_MEMBERSHIP_CHANGED,
            action="tenant.membership.update", outcome=AuditOutcome.SUCCESS,
            tenant_id=membership.tenant_id, actor_user_id=actor_membership.user_id,
            resource_type="tenant_membership", resource_id=membership.id,
            metadata={"target_role": membership.role.value, "membership_active": membership.is_active},
        )
        return membership
    except MembershipNotFoundError as exc:
        record_request_event(request, session, event_type=AuditEventType.TENANT_ADMIN_ACTION_FAILURE,
                             action="tenant.membership.update", outcome=AuditOutcome.FAILURE,
                             tenant_id=actor_membership.tenant_id, actor_user_id=actor_membership.user_id,
                             reason_code="MEMBERSHIP_NOT_FOUND")
        raise HTTPException(status_code=404, detail="Membership not found") from exc
    except SelfRemovalError as exc:
        record_request_event(request, session, event_type=AuditEventType.TENANT_ACCESS_DENIED,
                             action="tenant.membership.update", outcome=AuditOutcome.DENIED,
                             tenant_id=actor_membership.tenant_id, actor_user_id=actor_membership.user_id,
                             reason_code="SELF_REMOVAL_PROTECTED")
        raise HTTPException(status_code=409, detail="Transfer ownership before leaving this tenant") from exc
    except LastOwnerError as exc:
        record_request_event(request, session, event_type=AuditEventType.TENANT_ACCESS_DENIED,
                             action="tenant.membership.update", outcome=AuditOutcome.DENIED,
                             tenant_id=actor_membership.tenant_id, actor_user_id=actor_membership.user_id,
                             reason_code="LAST_OWNER_PROTECTED")
        raise HTTPException(status_code=409, detail="A tenant must retain an active owner") from exc
    except DuplicateMembershipError as exc:
        record_request_event(request, session, event_type=AuditEventType.TENANT_ADMIN_ACTION_FAILURE,
                             action="tenant.membership.update", outcome=AuditOutcome.FAILURE,
                             tenant_id=actor_membership.tenant_id, actor_user_id=actor_membership.user_id,
                             reason_code="MEMBERSHIP_EXISTS")
        raise HTTPException(status_code=409, detail="User already has an active tenant membership") from exc
    except TenantPermissionError as exc:
        record_request_event(request, session, event_type=AuditEventType.TENANT_ACCESS_DENIED,
                             action="tenant.membership.update", outcome=AuditOutcome.DENIED,
                             tenant_id=actor_membership.tenant_id, actor_user_id=actor_membership.user_id,
                             reason_code="ROLE_NOT_ALLOWED")
        raise HTTPException(status_code=403, detail="Insufficient tenant privileges") from exc
