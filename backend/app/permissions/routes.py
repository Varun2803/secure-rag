from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.audit.dependencies import record_request_event
from app.audit.events import AuditEventType, AuditOutcome
from app.database.postgres import get_db
from app.permissions.schemas import DocumentPermissionsPublic, PermissionReplaceRequest
from app.permissions.service import (
    PermissionAuditError, PermissionDatabaseError, PermissionDocumentNotFoundError,
    PermissionDocumentNotReadyError, get_document_permissions, replace_document_permissions,
)
from app.permissions.synchronization import (
    PermissionSyncPersistenceError, process_permission_sync_task,
)
from app.permissions.validators import PermissionAssignmentError, PermissionValidationUnavailable
from app.tenants.dependencies import TenantContextDependency, TenantContext
from app.tenants.models import TenantRole

router = APIRouter(
    prefix="/tenants/{tenant_id}/documents/{document_id}/permissions",
    tags=["document permissions"],
)
SessionDependency = Annotated[Session, Depends(get_db)]


def _require_manager(request: Request, session: Session, context: TenantContext) -> None:
    if context.role not in {TenantRole.OWNER, TenantRole.ADMIN}:
        record_request_event(
            request, session, event_type=AuditEventType.DOCUMENT_PERMISSION_UPDATE_FAILURE,
            action="permissions.authorize", outcome=AuditOutcome.DENIED,
            tenant_id=context.tenant_id, actor_user_id=context.user_id,
            resource_type="document", reason_code="INSUFFICIENT_ROLE",
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient tenant privileges")


def _check_tenant(request: Request, session: Session, tenant_id: UUID, context: TenantContext) -> None:
    if context.tenant_id != tenant_id:
        record_request_event(
            request, session, event_type=AuditEventType.DOCUMENT_PERMISSION_UPDATE_FAILURE,
            action="permissions.tenant.validate", outcome=AuditOutcome.DENIED,
            tenant_id=context.tenant_id, actor_user_id=context.user_id,
            resource_type="tenant", resource_id=tenant_id, reason_code="TENANT_MISMATCH",
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")


def _safe_failure_event(request: Request, session: Session, context: TenantContext,
                        document_id: UUID, reason_code: str) -> None:
    record_request_event(
        request, session, event_type=AuditEventType.DOCUMENT_PERMISSION_UPDATE_FAILURE,
        action="permissions.update", outcome=AuditOutcome.FAILURE,
        tenant_id=context.tenant_id, actor_user_id=context.user_id,
        resource_type="document", resource_id=document_id, reason_code=reason_code,
    )


@router.get("", response_model=DocumentPermissionsPublic)
def read_document_permissions(
    tenant_id: UUID,
    document_id: UUID,
    context: TenantContextDependency,
    session: SessionDependency,
    request: Request,
) -> DocumentPermissionsPublic:
    _check_tenant(request, session, tenant_id, context)
    _require_manager(request, session, context)
    try:
        permissions = get_document_permissions(session, tenant_id=context.tenant_id, document_id=document_id)
    except PermissionDocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Document not found") from exc
    except PermissionDatabaseError as exc:
        raise HTTPException(status_code=503, detail="Document permissions are unavailable") from exc
    record_request_event(
        request, session, event_type=AuditEventType.DOCUMENT_PERMISSION_READ,
        action="permissions.read", outcome=AuditOutcome.SUCCESS,
        tenant_id=context.tenant_id, actor_user_id=context.user_id,
        resource_type="document", resource_id=document_id,
        metadata={"allowed_role_count": len(permissions.allowed_roles),
                  "allowed_user_count": len(permissions.allowed_users),
                  "synchronization_status": permissions.synchronization_status.value},
    )
    return permissions


@router.put("", response_model=DocumentPermissionsPublic)
def update_document_permissions(
    tenant_id: UUID,
    document_id: UUID,
    payload: PermissionReplaceRequest,
    context: TenantContextDependency,
    session: SessionDependency,
    request: Request,
) -> DocumentPermissionsPublic:
    _check_tenant(request, session, tenant_id, context)
    _require_manager(request, session, context)
    try:
        update = replace_document_permissions(
            session,
            tenant_id=context.tenant_id,
            document_id=document_id,
            payload=payload,
            actor_user_id=context.user_id,
            request_id=request.state.request_id,
        )
    except PermissionDocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Document not found") from exc
    except PermissionDocumentNotReadyError as exc:
        _safe_failure_event(request, session, context, document_id, "DOCUMENT_NOT_READY")
        raise HTTPException(status_code=409, detail="Document permissions cannot be changed yet") from exc
    except PermissionAssignmentError as exc:
        _safe_failure_event(request, session, context, document_id, "INVALID_USER_ASSIGNMENT")
        raise HTTPException(status_code=422, detail="Invalid document permission assignments") from exc
    except PermissionValidationUnavailable as exc:
        session.rollback()
        _safe_failure_event(request, session, context, document_id, "VALIDATION_UNAVAILABLE")
        raise HTTPException(status_code=503, detail="Document permissions could not be validated") from exc
    except PermissionAuditError as exc:
        session.rollback()
        _safe_failure_event(request, session, context, document_id, "AUDIT_FAILURE")
        raise HTTPException(status_code=503, detail="Document permissions could not be updated") from exc
    except PermissionDatabaseError as exc:
        session.rollback()
        _safe_failure_event(request, session, context, document_id, "DATABASE_FAILURE")
        raise HTTPException(status_code=503, detail="Document permissions could not be updated") from exc

    try:
        sync = process_permission_sync_task(
            session, task_id=update.sync_task.id, actor_user_id=context.user_id,
            request_id=getattr(request.state, "request_id", None),
        )
    except PermissionSyncPersistenceError as exc:
        raise HTTPException(status_code=503, detail="Permission synchronization is pending") from exc

    return DocumentPermissionsPublic(
        document_id=update.document.id,
        tenant_id=update.document.tenant_id,
        classification=payload.classification.name,
        is_public=update.document.is_public,
        allowed_roles=payload.allowed_roles,
        allowed_users=[UUID(value) for value in update.document.allowed_users],
        permission_updated_at=update.document.permission_updated_at,
        synchronization_status=sync.status,
    )
