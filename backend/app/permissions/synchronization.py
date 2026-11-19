import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from qdrant_client import QdrantClient, models as qdrant_models
from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.audit.events import AuditEventType, AuditOutcome
from app.audit.service import stage_audit_event
from app.config import get_settings
from app.database.qdrant import get_qdrant_client
from app.documents.models import Document, DocumentStatus
from app.permissions.models import DocumentPermissionSync, PermissionSyncStatus

logger = logging.getLogger(__name__)
PROCESSING_LEASE = timedelta(minutes=5)
MAX_SYNC_BATCH = 100


class PermissionSyncPersistenceError(RuntimeError):
    """The outbox state could not be durably advanced."""


@dataclass(frozen=True, slots=True)
class PermissionSyncResult:
    synchronized: bool
    status: PermissionSyncStatus
    attempts: int


def _document_filter(tenant_id: UUID, document_id: UUID) -> qdrant_models.Filter:
    return qdrant_models.Filter(must=[
        qdrant_models.FieldCondition(
            key="tenant_id", match=qdrant_models.MatchValue(value=str(tenant_id)),
        ),
        qdrant_models.FieldCondition(
            key="document_id", match=qdrant_models.MatchValue(value=str(document_id)),
        ),
    ])


def _synchronize_latest_acl(session: Session, task: DocumentPermissionSync, client: QdrantClient) -> None:
    document = session.scalar(select(Document).where(
        Document.id == task.document_id, Document.tenant_id == task.tenant_id,
    ).execution_options(populate_existing=True))
    if document is None:
        raise ValueError("DOCUMENT_MISSING")
    if document.status != DocumentStatus.COMPLETED:
        raise ValueError("DOCUMENT_NOT_READY")
    collection = get_settings().qdrant_collection_name
    scope = _document_filter(task.tenant_id, task.document_id)
    point_count = client.count(collection_name=collection, count_filter=scope, exact=True).count
    if point_count != document.chunk_count or point_count <= 0:
        raise ValueError("CHUNK_COUNT_MISMATCH")
    update = client.set_payload(
        collection_name=collection,
        payload={
            "tenant_id": str(document.tenant_id),
            "document_id": str(document.id),
            "classification": document.classification,
            "is_public": document.is_public,
            "allowed_roles": list(document.allowed_roles),
            "allowed_users": list(document.allowed_users),
            "uploaded_by": str(document.uploaded_by),
        },
        points=scope,
        wait=True,
    )
    if getattr(getattr(update, "status", None), "value", getattr(update, "status", None)) != "completed":
        raise ValueError("QDRANT_UPDATE_NOT_CONFIRMED")


def _stage_sync_audit(
    session: Session,
    *,
    task: DocumentPermissionSync,
    actor_user_id: UUID | None,
    request_id: UUID | None,
    event_type: AuditEventType,
    action: str,
    outcome: AuditOutcome,
    reason_code: str | None = None,
) -> None:
    stage_audit_event(
        session,
        event_type=event_type,
        action=action,
        outcome=outcome,
        tenant_id=task.tenant_id,
        actor_user_id=actor_user_id,
        resource_type="document",
        resource_id=task.document_id,
        request_id=request_id,
        reason_code=reason_code,
        metadata={"attempts": task.attempts, "synchronization_status": task.status},
    )


def process_permission_sync_task(
    session: Session,
    *,
    task_id: UUID,
    actor_user_id: UUID | None = None,
    request_id: UUID | None = None,
    client: QdrantClient | None = None,
) -> PermissionSyncResult:
    """Claim and process one current ACL revision; failures remain retryable PENDING."""
    now = datetime.now(timezone.utc)
    try:
        task = session.scalar(select(DocumentPermissionSync).where(
            DocumentPermissionSync.id == task_id,
            or_(
                DocumentPermissionSync.status == PermissionSyncStatus.PENDING.value,
                DocumentPermissionSync.status == PermissionSyncStatus.PROCESSING.value,
                DocumentPermissionSync.status == PermissionSyncStatus.COMPLETED.value,
            ),
        ).with_for_update(skip_locked=True).execution_options(populate_existing=True))
        if task is None:
            return PermissionSyncResult(False, PermissionSyncStatus.PENDING, 0)
        if task.status == PermissionSyncStatus.COMPLETED.value:
            return PermissionSyncResult(True, PermissionSyncStatus.COMPLETED, task.attempts)
        if task.status == PermissionSyncStatus.PROCESSING.value and task.updated_at:
            lease_updated_at = task.updated_at
            if lease_updated_at.tzinfo is None:
                # SQLite drops timezone info for DateTime(timezone=True); PostgreSQL
                # values remain aware. Treat stored naive test/legacy values as UTC.
                lease_updated_at = lease_updated_at.replace(tzinfo=timezone.utc)
            if now - lease_updated_at < PROCESSING_LEASE:
                return PermissionSyncResult(False, PermissionSyncStatus.PROCESSING, task.attempts)
        task.status = PermissionSyncStatus.PROCESSING.value
        task.attempts += 1
        task.updated_at = now
        claimed_revision = task.revision
        if task.attempts > 1:
            _stage_sync_audit(
                session, task=task, actor_user_id=actor_user_id, request_id=request_id,
                event_type=AuditEventType.DOCUMENT_PERMISSION_SYNC_RETRY,
                action="permissions.sync.retry", outcome=AuditOutcome.SUCCESS,
            )
        session.commit()
        session.refresh(task)
    except Exception as exc:
        session.rollback()
        raise PermissionSyncPersistenceError from exc

    qdrant_client = client
    failure = False
    try:
        qdrant_client = qdrant_client or get_qdrant_client()
        _synchronize_latest_acl(session, task, qdrant_client)
    except Exception as exc:
        # Only a fixed failure code and exception class are logged or persisted.
        logger.warning("Document permission synchronization failed (%s)", type(exc).__name__)
        failure = True

    superseded = False
    try:
        current = session.scalar(select(DocumentPermissionSync).where(
            DocumentPermissionSync.id == task_id,
        ).with_for_update().execution_options(populate_existing=True))
        if current is None:
            raise SQLAlchemyError("permission sync task disappeared")
        if current.revision != claimed_revision:
            # A newer ACL was committed while this update was in flight; keep the
            # coalesced task pending so the next attempt reloads that latest ACL.
            current.status = PermissionSyncStatus.PENDING.value
            current.last_error_code = None
            current.completed_at = None
            current.updated_at = datetime.now(timezone.utc)
            superseded = True
            _stage_sync_audit(
                session, task=current, actor_user_id=actor_user_id, request_id=request_id,
                event_type=AuditEventType.DOCUMENT_PERMISSION_SYNC_RETRY,
                action="permissions.sync.superseded", outcome=AuditOutcome.SUCCESS,
                reason_code="SUPERSEDED_REVISION",
            )
        elif failure:
            current.status = PermissionSyncStatus.PENDING.value
            current.last_error_code = "QDRANT_SYNC_FAILED"
            current.completed_at = None
            current.updated_at = datetime.now(timezone.utc)
            _stage_sync_audit(
                session, task=current, actor_user_id=actor_user_id, request_id=request_id,
                event_type=AuditEventType.DOCUMENT_PERMISSION_SYNC_FAILURE,
                action="permissions.sync.failure", outcome=AuditOutcome.FAILURE,
                reason_code="QDRANT_SYNC_FAILED",
            )
        else:
            current.status = PermissionSyncStatus.COMPLETED.value
            current.last_error_code = None
            current.completed_at = datetime.now(timezone.utc)
            current.updated_at = current.completed_at
            _stage_sync_audit(
                session, task=current, actor_user_id=actor_user_id, request_id=request_id,
                event_type=AuditEventType.DOCUMENT_PERMISSION_SYNC_SUCCESS,
                action="permissions.sync.complete", outcome=AuditOutcome.SUCCESS,
            )
        session.commit()
        session.refresh(current)
    except Exception as exc:
        session.rollback()
        raise PermissionSyncPersistenceError from exc

    if superseded:
        result = PermissionSyncResult(False, PermissionSyncStatus.PENDING, current.attempts)
    elif failure:
        result = PermissionSyncResult(False, PermissionSyncStatus.PENDING, current.attempts)
    else:
        result = PermissionSyncResult(True, PermissionSyncStatus.COMPLETED, current.attempts)

    return result


def process_pending_permission_syncs(
    session: Session, *, limit: int = 50, client: QdrantClient | None = None,
) -> list[PermissionSyncResult]:
    """Internal maintenance/worker entry point; no public route is registered."""
    if not 1 <= limit <= MAX_SYNC_BATCH:
        raise ValueError("Invalid permission synchronization batch size")
    cutoff = datetime.now(timezone.utc) - PROCESSING_LEASE
    tasks = session.scalars(select(DocumentPermissionSync).where(
        or_(
            DocumentPermissionSync.status == PermissionSyncStatus.PENDING.value,
            (DocumentPermissionSync.status == PermissionSyncStatus.PROCESSING.value) &
            (DocumentPermissionSync.updated_at < cutoff),
        )
    ).order_by(DocumentPermissionSync.created_at, DocumentPermissionSync.id).limit(limit)).all()
    outcomes = []
    for task in tasks:
        outcomes.append(process_permission_sync_task(session, task_id=task.id, client=client))
    return outcomes
