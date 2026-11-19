from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.audit.events import AuditEventType, AuditOutcome
from app.audit.service import stage_audit_event
from app.documents.models import Document, DocumentClassification, DocumentStatus
from app.permissions.models import DocumentPermissionSync, PermissionSyncStatus
from app.permissions.schemas import DocumentPermissionsPublic, PermissionReplaceRequest
from app.permissions.validators import validate_allowed_users


class PermissionDocumentNotFoundError(LookupError):
    """Document does not exist within the verified tenant."""


class PermissionDocumentNotReadyError(RuntimeError):
    """Document is not yet a completed vectorized resource."""


class PermissionDatabaseError(RuntimeError):
    """Permission data could not be safely loaded or persisted."""


class PermissionAuditError(RuntimeError):
    """Permission changes could not be committed with their audit events."""


@dataclass(frozen=True, slots=True)
class PermissionUpdate:
    document: Document
    sync_task: DocumentPermissionSync
    changed_fields: list[str]
    revoked: bool
    previous_classification: str
    roles_added_count: int
    roles_removed_count: int
    users_added_count: int
    users_removed_count: int


def _load_document(session: Session, tenant_id: UUID, document_id: UUID, *, lock: bool = False) -> Document:
    query = select(Document).where(Document.tenant_id == tenant_id, Document.id == document_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    try:
        document = session.scalar(query)
    except SQLAlchemyError as exc:
        raise PermissionDatabaseError from exc
    if document is None:
        raise PermissionDocumentNotFoundError
    return document


def get_document_permissions(
    session: Session, *, tenant_id: UUID, document_id: UUID,
) -> DocumentPermissionsPublic:
    document = _load_document(session, tenant_id, document_id)
    try:
        task = session.scalar(select(DocumentPermissionSync).where(
            DocumentPermissionSync.tenant_id == tenant_id,
            DocumentPermissionSync.document_id == document_id,
        ))
    except SQLAlchemyError as exc:
        raise PermissionDatabaseError from exc
    status = PermissionSyncStatus(task.status) if task else PermissionSyncStatus.COMPLETED
    return DocumentPermissionsPublic(
        document_id=document.id,
        tenant_id=document.tenant_id,
        classification=DocumentClassification(document.classification).name,
        is_public=document.is_public,
        allowed_roles=document.allowed_roles,
        allowed_users=[UUID(value) for value in document.allowed_users],
        permission_updated_at=document.permission_updated_at,
        synchronization_status=status,
    )


def replace_document_permissions(
    session: Session,
    *,
    tenant_id: UUID,
    document_id: UUID,
    payload: PermissionReplaceRequest,
    actor_user_id: UUID,
    request_id: UUID,
) -> PermissionUpdate:
    """Replace PostgreSQL ACL and enqueue the latest Qdrant revision atomically."""
    document = _load_document(session, tenant_id, document_id, lock=True)
    if document.status != DocumentStatus.COMPLETED:
        raise PermissionDocumentNotReadyError
    normalized_users = validate_allowed_users(session, tenant_id, payload.allowed_users)
    previous_classification = document.classification
    previous_public = document.is_public
    previous_roles = list(document.allowed_roles)
    previous_users = list(document.allowed_users)
    roles = [role.value for role in payload.allowed_roles]
    changed_fields = []
    if previous_classification != payload.classification.value:
        changed_fields.append("classification")
    if previous_public != payload.is_public:
        changed_fields.append("is_public")
    if previous_roles != roles:
        changed_fields.append("allowed_roles")
    if previous_users != normalized_users:
        changed_fields.append("allowed_users")

    now = datetime.now(timezone.utc)
    revoked = not payload.is_public and (
        previous_public or
        not set(previous_roles).issubset(set(roles)) or
        not set(previous_users).issubset(set(normalized_users))
    )
    try:
        task = session.scalar(select(DocumentPermissionSync).where(
            DocumentPermissionSync.tenant_id == tenant_id,
            DocumentPermissionSync.document_id == document_id,
        ).with_for_update().execution_options(populate_existing=True))
        document.classification = payload.classification.value
        document.is_public = payload.is_public
        document.allowed_roles = roles
        document.allowed_users = normalized_users
        document.permission_updated_at = now
        if task is None:
            task = DocumentPermissionSync(
                tenant_id=tenant_id, document_id=document_id,
                status=PermissionSyncStatus.PENDING.value, attempts=0, revision=1,
            )
            session.add(task)
        else:
            task.status = PermissionSyncStatus.PENDING.value
            task.revision += 1
            task.attempts = 0
            task.last_error_code = None
            task.completed_at = None
            task.updated_at = now
        audit_metadata = {
            "changed_fields": changed_fields,
            "previous_classification": DocumentClassification(previous_classification).name,
            "new_classification": payload.classification.name,
            "allowed_role_count": len(roles),
            "allowed_user_count": len(normalized_users),
            "roles_added_count": len(set(roles) - set(previous_roles)),
            "roles_removed_count": len(set(previous_roles) - set(roles)),
            "users_added_count": len(set(normalized_users) - set(previous_users)),
            "users_removed_count": len(set(previous_users) - set(normalized_users)),
            "is_public": payload.is_public,
            "synchronization_status": PermissionSyncStatus.PENDING.value,
        }
        try:
            stage_audit_event(
                session,
                event_type=AuditEventType.DOCUMENT_PERMISSION_UPDATE,
                action="permissions.update",
                outcome=AuditOutcome.SUCCESS,
                tenant_id=tenant_id,
                actor_user_id=actor_user_id,
                resource_type="document",
                resource_id=document_id,
                request_id=request_id,
                metadata=audit_metadata,
            )
            if revoked:
                stage_audit_event(
                    session,
                    event_type=AuditEventType.DOCUMENT_PERMISSION_REVOKED,
                    action="permissions.revoke",
                    outcome=AuditOutcome.SUCCESS,
                    tenant_id=tenant_id,
                    actor_user_id=actor_user_id,
                    resource_type="document",
                    resource_id=document_id,
                    request_id=request_id,
                    metadata={
                        "allowed_role_count": len(roles),
                        "allowed_user_count": len(normalized_users),
                        "synchronization_status": PermissionSyncStatus.PENDING.value,
                    },
                )
        except Exception as exc:
            session.rollback()
            raise PermissionAuditError from exc
        session.commit()
        session.refresh(document)
        session.refresh(task)
    except SQLAlchemyError as exc:
        session.rollback()
        raise PermissionDatabaseError from exc
    return PermissionUpdate(
        document=document,
        sync_task=task,
        changed_fields=changed_fields,
        revoked=revoked,
        previous_classification=DocumentClassification(previous_classification).name,
        roles_added_count=len(set(roles) - set(previous_roles)),
        roles_removed_count=len(set(previous_roles) - set(roles)),
        users_added_count=len(set(normalized_users) - set(previous_users)),
        users_removed_count=len(set(previous_users) - set(normalized_users)),
    )
