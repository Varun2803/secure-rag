import json
import logging
import math
import re
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.audit.events import AuditEventType, AuditOutcome
from app.audit.models import AuditLog
from app.audit.schemas import AuditEventPage, AuditEventPublic

logger = logging.getLogger(__name__)
MAX_METADATA_BYTES = 4096
_ACTION = re.compile(r"[a-z][a-z0-9_.-]{0,79}\Z")
_REASON = re.compile(r"[A-Z0-9_]{1,64}\Z")
_RESOURCE_TYPE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_SAFE_METADATA_TYPES: dict[str, type | tuple[type, ...]] = {
    "top_k": int, "result_count": int, "duration_ms": int, "status_category": str,
    "provider_category": str, "citation_count": int, "grounded": bool,
    "authorized_context_count": int, "file_size": int, "content_type": str,
    "classification": int, "is_public": bool, "membership_role": str,
    "target_role": str, "membership_active": bool, "changed_fields": list,
    "page_count": int, "chunk_count": int, "operation": str,
    "allowed_role_count": int, "allowed_user_count": int, "previous_classification": str,
    "new_classification": str, "synchronization_status": str, "attempts": int,
    "roles_added_count": int, "roles_removed_count": int,
    "users_added_count": int, "users_removed_count": int,
}
_SENSITIVE_FRAGMENTS = (
    "password", "token", "authorization", "api_key", "secret", "prompt", "content",
    "credential", "raw_response", "generated_answer", "document_text", "file_path",
)
_SAFE_STRING_VALUES = {
    "status_category": {"UNAVAILABLE", "INTERNAL_ERROR", "SUCCESS", "EMPTY", "INVALID", "ABSTAINED", "FAILURE"},
    "provider_category": {"openai_compatible", "local", "unknown"},
    "content_type": {"application/pdf"},
    "membership_role": {"OWNER", "ADMIN", "HR", "MANAGER", "EMPLOYEE"},
    "target_role": {"OWNER", "ADMIN", "HR", "MANAGER", "EMPLOYEE"},
    "previous_classification": {"PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"},
    "new_classification": {"PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"},
    "synchronization_status": {"PENDING", "PROCESSING", "COMPLETED"},
    "operation": {"create", "update", "delete", "upload", "ingest", "search", "answer", "login"},
}
_SAFE_CHANGED_FIELDS = {
    "name", "description", "role", "is_active", "classification", "is_public", "allowed_roles", "allowed_users",
}


class AuditPersistenceError(RuntimeError):
    """A required audit event could not be durably persisted."""


class AuditMetadataError(ValueError):
    """Audit metadata violates the explicit safe-field policy."""


def sanitize_metadata(metadata: dict[str, Any] | None) -> dict[str, object]:
    if metadata is None:
        return {}
    if not isinstance(metadata, dict):
        raise AuditMetadataError("Audit metadata must be a flat object")
    sanitized: dict[str, object] = {}
    for key, value in metadata.items():
        if not isinstance(key, str):
            raise AuditMetadataError("Audit metadata contains an unsupported field")
        normalized_key = key.casefold().replace("-", "_")
        if key not in _SAFE_METADATA_TYPES and any(fragment in normalized_key for fragment in _SENSITIVE_FRAGMENTS):
            raise AuditMetadataError("Sensitive fields are not allowed in audit metadata")
        expected = _SAFE_METADATA_TYPES.get(key)
        if expected is None or not isinstance(value, expected):
            raise AuditMetadataError("Audit metadata contains an unsupported field or value")
        if key in {"top_k", "result_count", "duration_ms", "authorized_context_count", "file_size",
                   "classification", "page_count", "chunk_count", "citation_count", "allowed_role_count",
                   "allowed_user_count", "attempts", "roles_added_count", "roles_removed_count",
                   "users_added_count", "users_removed_count"}:
            if isinstance(value, bool) or value < 0:
                raise AuditMetadataError("Audit metadata contains an invalid number")
        if isinstance(value, str) and (len(value) > 200 or "\x00" in value):
            raise AuditMetadataError("Audit metadata string exceeds the allowed limit")
        allowed_values = _SAFE_STRING_VALUES.get(key)
        if allowed_values is not None and value not in allowed_values:
            raise AuditMetadataError("Audit metadata contains an unsupported value")
        if isinstance(value, list):
            if (key != "changed_fields" or len(value) > 20 or
                    any(not isinstance(item, str) or item not in _SAFE_CHANGED_FIELDS for item in value)):
                raise AuditMetadataError("Audit metadata list exceeds the allowed limit")
        if isinstance(value, float) and not math.isfinite(value):
            raise AuditMetadataError("Audit metadata contains a non-finite number")
        sanitized[key] = list(value) if isinstance(value, list) else value
    try:
        size = len(json.dumps(sanitized, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise AuditMetadataError("Audit metadata is not serializable") from exc
    if size > MAX_METADATA_BYTES:
        raise AuditMetadataError("Audit metadata exceeds the allowed size")
    return sanitized


def stage_audit_event(
    session: Session,
    *,
    event_type: AuditEventType | str,
    action: str,
    outcome: AuditOutcome | str,
    tenant_id: UUID | None = None,
    actor_user_id: UUID | None = None,
    resource_type: str | None = None,
    resource_id: UUID | str | None = None,
    request_id: UUID | None = None,
    reason_code: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    """Add a validated audit event to the caller's uncommitted transaction.

    The helper never commits or rolls back. The caller must commit the surrounding
    transaction and treat a uniqueness conflict as a transaction failure unless an
    existing matching event was found before insertion.
    """
    try:
        event_value = AuditEventType(event_type).value
        outcome_value = AuditOutcome(outcome).value
    except ValueError as exc:
        raise ValueError("Unsupported audit event type or outcome") from exc
    if not isinstance(action, str) or not _ACTION.fullmatch(action):
        raise ValueError("Invalid audit action")
    if any(value is not None and not isinstance(value, UUID)
           for value in (tenant_id, actor_user_id, request_id)):
        raise ValueError("Audit identifier fields must be UUIDs")
    if reason_code is not None and (not isinstance(reason_code, str) or not _REASON.fullmatch(reason_code)):
        raise ValueError("Invalid audit reason code")
    if resource_type is not None and (not isinstance(resource_type, str) or not _RESOURCE_TYPE.fullmatch(resource_type)):
        raise ValueError("Invalid audit resource type")
    if resource_id is not None and not isinstance(resource_id, (UUID, str)):
        raise ValueError("Invalid audit resource identifier")
    resource_value = str(resource_id) if resource_id is not None else None
    if resource_value is not None and len(resource_value) > 100:
        raise ValueError("Invalid audit resource identifier")
    clean_metadata = sanitize_metadata(metadata)
    correlation_id = request_id or uuid4()
    for pending in session.new:
        if (isinstance(pending, AuditLog) and pending.request_id == correlation_id and
                pending.event_type == event_value and pending.action == action):
            return pending
    try:
        existing = session.scalar(select(AuditLog).where(
            AuditLog.request_id == correlation_id,
            AuditLog.event_type == event_value,
            AuditLog.action == action,
        ))
    except SQLAlchemyError as exc:
        raise AuditPersistenceError("Audit event could not be staged") from exc
    if existing is not None:
        return existing
    record = AuditLog(
        tenant_id=tenant_id, actor_user_id=actor_user_id, event_type=event_value,
        action=action, outcome=outcome_value, resource_type=resource_type,
        resource_id=resource_value, reason_code=reason_code, request_id=correlation_id,
        metadata_json=clean_metadata,
    )
    session.add(record)
    return record


def create_audit_event(
    session: Session,
    *,
    event_type: AuditEventType | str,
    action: str,
    outcome: AuditOutcome | str,
    tenant_id: UUID | None = None,
    actor_user_id: UUID | None = None,
    resource_type: str | None = None,
    resource_id: UUID | str | None = None,
    request_id: UUID | None = None,
    reason_code: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    """Persist one event in its own explicit transaction (legacy workflow API)."""
    if session.new or session.dirty or session.deleted:
        raise AuditPersistenceError("Cannot persist an audit event while business changes are pending")
    correlation_id = request_id or uuid4()
    event_value = AuditEventType(event_type).value
    try:
        record = stage_audit_event(
            session,
            event_type=event_type,
            action=action,
            outcome=outcome,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            request_id=correlation_id,
            reason_code=reason_code,
            metadata=metadata,
        )
        if record not in session.new:
            return record
        session.flush()
        session.commit()
    except (SQLAlchemyError, IntegrityError, AuditPersistenceError) as exc:
        session.rollback()
        # Another attempt with the same request ID may have won the uniqueness race.
        try:
            duplicate = session.scalar(select(AuditLog).where(
                AuditLog.request_id == correlation_id,
                AuditLog.event_type == event_value,
                AuditLog.action == action,
            ))
        except SQLAlchemyError:
            duplicate = None
        if duplicate is not None:
            return duplicate
        logger.error("Audit event persistence failed (%s)", type(exc).__name__)
        raise AuditPersistenceError("Audit event could not be persisted") from exc
    return record


def query_audit_events(
    session: Session,
    *,
    context: object,
    event_type: AuditEventType | None = None,
    actor_user_id: UUID | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    outcome: AuditOutcome | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> AuditEventPage:
    """Internal query API requiring verified tenant context for every query."""
    tenant_id = getattr(context, "tenant_id", None)
    if not isinstance(tenant_id, UUID):
        raise ValueError("Verified tenant context is required")
    if not 1 <= limit <= 100 or offset < 0:
        raise ValueError("Invalid audit pagination")
    if start_at is not None and start_at.tzinfo is None:
        raise ValueError("Audit date filters must be timezone-aware")
    if end_at is not None and end_at.tzinfo is None:
        raise ValueError("Audit date filters must be timezone-aware")
    if start_at is not None and end_at is not None and start_at > end_at:
        raise ValueError("Audit start date must precede end date")
    filters = [AuditLog.tenant_id == tenant_id]
    if event_type is not None:
        filters.append(AuditLog.event_type == AuditEventType(event_type).value)
    if actor_user_id is not None:
        filters.append(AuditLog.actor_user_id == actor_user_id)
    if resource_type is not None:
        filters.append(AuditLog.resource_type == resource_type)
    if resource_id is not None:
        filters.append(AuditLog.resource_id == str(resource_id))
    if outcome is not None:
        filters.append(AuditLog.outcome == AuditOutcome(outcome).value)
    if start_at is not None:
        filters.append(AuditLog.created_at >= start_at)
    if end_at is not None:
        filters.append(AuditLog.created_at <= end_at)
    total = session.scalar(select(func.count()).select_from(AuditLog).where(*filters)) or 0
    records = session.scalars(
        select(AuditLog).where(*filters).order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(limit).offset(offset)
    ).all()
    return AuditEventPage(items=[AuditEventPublic.model_validate(item) for item in records],
                          total=total, limit=limit, offset=offset)
