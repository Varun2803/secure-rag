import logging
import re
from pathlib import Path, PurePosixPath
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.audit.dependencies import record_request_event
from app.audit.events import AuditEventType, AuditOutcome
from app.database.postgres import get_db
from app.documents.models import DocumentClassification
from app.documents.schemas import DocumentPage, DocumentPublic, DocumentStatusPublic
from app.documents.service import (
    DocumentNotFoundError, DuplicateDocumentError, IngestionFailedError,
    get_document, ingest_document, list_documents,
)
from app.documents.storage import UploadTooLargeError, store_upload
from app.tenants.dependencies import TenantContextDependency
from app.tenants.models import TenantMembership, TenantRole
from app.users.models import User

router = APIRouter(prefix="/tenants/{tenant_id}/documents", tags=["documents"])
SessionDependency = Annotated[Session, Depends(get_db)]
TenantContextParam = TenantContextDependency
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
logger = logging.getLogger(__name__)


def _safe_original_filename(filename: str | None) -> str:
    value = (filename or "").replace("\\", "/").split("/")[-1].strip()
    value = _CONTROL_CHARS.sub("", value)
    if not value or value in {".", ".."}:
        raise HTTPException(status_code=422, detail="Invalid filename")
    if len(value) > get_settings().max_filename_length:
        raise HTTPException(status_code=422, detail="Filename exceeds the allowed length")
    return value


def _validate_acl(
    session: Session, tenant_id: UUID, roles: list[str], users: list[str]
) -> tuple[list[str], list[str]]:
    try:
        normalized_roles = [TenantRole(role).value for role in roles]
        normalized_users = [str(UUID(value)) for value in users]
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="Invalid document access metadata") from exc
    if len(set(normalized_roles)) != len(normalized_roles) or len(set(normalized_users)) != len(normalized_users):
        raise HTTPException(status_code=422, detail="Duplicate document access metadata")
    if normalized_users:
        try:
            user_ids = [UUID(user_id) for user_id in normalized_users]
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
            raise HTTPException(status_code=503, detail="Document access metadata could not be validated") from exc
        if eligible != set(user_ids):
            raise HTTPException(status_code=422, detail="Invalid document access assignments")
    return normalized_roles, normalized_users


def _remove_failed_upload(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except Exception as exc:
        logger.error("Failed to remove temporary document upload (%s)", type(exc).__name__)


@router.post("/upload", response_model=DocumentPublic, status_code=status.HTTP_201_CREATED)
async def upload_document(
    tenant_id: UUID,
    context: TenantContextParam,
    session: SessionDependency,
    file: UploadFile,
    request: Request,
    classification: Annotated[DocumentClassification, Form()] = DocumentClassification.INTERNAL,
    is_public: Annotated[bool, Form()] = False,
    allowed_roles: Annotated[list[str] | None, Form()] = None,
    allowed_users: Annotated[list[str] | None, Form()] = None,
) -> DocumentPublic:
    if context.role not in {TenantRole.OWNER, TenantRole.ADMIN, TenantRole.HR}:
        record_request_event(
            request, session, event_type=AuditEventType.TENANT_ACCESS_DENIED,
            action="document.upload.authorize", outcome=AuditOutcome.DENIED,
            tenant_id=context.tenant_id, actor_user_id=context.user_id,
            resource_type="tenant", resource_id=context.tenant_id,
            reason_code="INSUFFICIENT_ROLE",
            metadata={"membership_role": context.role.value},
        )
        record_request_event(
            request, session, event_type=AuditEventType.DOCUMENT_UPLOAD_FAILURE,
            action="document.upload", outcome=AuditOutcome.DENIED,
            tenant_id=context.tenant_id, actor_user_id=context.user_id,
            resource_type="tenant", resource_id=context.tenant_id,
            reason_code="INSUFFICIENT_ROLE",
        )
        raise HTTPException(status_code=403, detail="Insufficient tenant privileges")
    if context.tenant_id != tenant_id:
        record_request_event(
            request, session, event_type=AuditEventType.TENANT_ACCESS_DENIED,
            action="document.upload.authorize", outcome=AuditOutcome.DENIED,
            tenant_id=context.tenant_id, actor_user_id=context.user_id,
            resource_type="tenant", resource_id=tenant_id,
            reason_code="TENANT_MISMATCH",
        )
        raise HTTPException(status_code=404, detail="Tenant not found")
    try:
        original_filename = _safe_original_filename(file.filename)
        if PurePosixPath(original_filename).suffix.casefold() != ".pdf" or (file.content_type or "").casefold() != "application/pdf":
            raise HTTPException(status_code=415, detail="Only PDF uploads are supported")
        role_values, user_values = _validate_acl(session, context.tenant_id, allowed_roles or [], allowed_users or [])
    except HTTPException:
        await file.close()
        session.rollback()
        record_request_event(
            request, session, event_type=AuditEventType.DOCUMENT_UPLOAD_FAILURE,
            action="document.upload", outcome=AuditOutcome.FAILURE,
            tenant_id=context.tenant_id, actor_user_id=context.user_id,
            reason_code="INVALID_UPLOAD_REQUEST",
        )
        raise
    record_request_event(
        request, session, event_type=AuditEventType.DOCUMENT_UPLOAD_STARTED,
        action="document.upload", outcome=AuditOutcome.SUCCESS,
        tenant_id=context.tenant_id, actor_user_id=context.user_id,
        resource_type="tenant", resource_id=context.tenant_id,
        metadata={"classification": classification.value, "is_public": is_public},
    )
    try:
        stored = await store_upload(file)
    except UploadTooLargeError as exc:
        record_request_event(
            request, session, event_type=AuditEventType.DOCUMENT_UPLOAD_FAILURE,
            action="document.upload", outcome=AuditOutcome.FAILURE,
            tenant_id=context.tenant_id, actor_user_id=context.user_id,
            reason_code="UPLOAD_TOO_LARGE",
        )
        raise HTTPException(status_code=413, detail="Uploaded file exceeds the size limit") from exc
    except ValueError as exc:
        record_request_event(
            request, session, event_type=AuditEventType.DOCUMENT_UPLOAD_FAILURE,
            action="document.upload", outcome=AuditOutcome.FAILURE,
            tenant_id=context.tenant_id, actor_user_id=context.user_id,
            reason_code="UPLOAD_INVALID",
        )
        raise HTTPException(status_code=422, detail="Invalid uploaded file") from exc
    except Exception as exc:
        session.rollback()
        record_request_event(
            request, session, event_type=AuditEventType.DOCUMENT_UPLOAD_FAILURE,
            action="document.upload", outcome=AuditOutcome.FAILURE,
            tenant_id=context.tenant_id, actor_user_id=context.user_id,
            reason_code="STORAGE_FAILED",
        )
        raise HTTPException(status_code=503, detail="Uploaded file could not be stored") from exc
    try:
        document = ingest_document(
            session,
            context=context,
            upload=stored,
            original_filename=original_filename,
            classification=classification,
            is_public=is_public,
            allowed_roles=role_values,
            allowed_users=user_values,
        )
    except DuplicateDocumentError as exc:
        _remove_failed_upload(stored.path)
        record_request_event(
            request, session, event_type=AuditEventType.DOCUMENT_UPLOAD_FAILURE,
            action="document.upload", outcome=AuditOutcome.FAILURE,
            tenant_id=context.tenant_id, actor_user_id=context.user_id,
            reason_code="DUPLICATE_DOCUMENT",
        )
        raise HTTPException(status_code=409, detail="This document already exists in the tenant") from exc
    except IngestionFailedError as exc:
        _remove_failed_upload(stored.path)
        record_request_event(
            request, session, event_type=AuditEventType.DOCUMENT_UPLOAD_FAILURE,
            action="document.upload", outcome=AuditOutcome.FAILURE,
            tenant_id=context.tenant_id, actor_user_id=context.user_id,
            resource_type="document", resource_id=exc.document.id,
            reason_code=exc.code,
        )
        record_request_event(
            request, session, event_type=AuditEventType.DOCUMENT_INGESTION_FAILURE,
            action="document.ingestion", outcome=AuditOutcome.FAILURE,
            tenant_id=context.tenant_id, actor_user_id=context.user_id,
            resource_type="document", resource_id=exc.document.id,
            reason_code=exc.code,
        )
        failure_status = 422 if exc.code == "PDF_INVALID" else 503
        raise HTTPException(
            status_code=failure_status,
            detail={"message": "Document processing failed", "document_id": str(exc.document.id), "code": exc.code},
        ) from exc
    except Exception as exc:
        _remove_failed_upload(stored.path)
        session.rollback()
        record_request_event(
            request, session, event_type=AuditEventType.DOCUMENT_UPLOAD_FAILURE,
            action="document.upload", outcome=AuditOutcome.FAILURE,
            tenant_id=context.tenant_id, actor_user_id=context.user_id,
            reason_code="PROCESSING_FAILED",
        )
        record_request_event(
            request, session, event_type=AuditEventType.DOCUMENT_INGESTION_FAILURE,
            action="document.ingestion", outcome=AuditOutcome.FAILURE,
            tenant_id=context.tenant_id, actor_user_id=context.user_id,
            reason_code="PROCESSING_FAILED",
        )
        raise HTTPException(status_code=503, detail="Document could not be stored") from exc
    document = DocumentPublic.model_validate(document)
    record_request_event(
        request, session, event_type=AuditEventType.DOCUMENT_UPLOAD_SUCCESS,
        action="document.upload", outcome=AuditOutcome.SUCCESS,
        tenant_id=context.tenant_id, actor_user_id=context.user_id,
        resource_type="document", resource_id=document.id,
        metadata={"file_size": document.file_size, "content_type": document.content_type,
                  "classification": document.classification, "page_count": document.page_count,
                  "chunk_count": document.chunk_count},
    )
    record_request_event(
        request, session, event_type=AuditEventType.DOCUMENT_INGESTION_SUCCESS,
        action="document.ingestion", outcome=AuditOutcome.SUCCESS,
        tenant_id=context.tenant_id, actor_user_id=context.user_id,
        resource_type="document", resource_id=document.id,
        metadata={"page_count": document.page_count, "chunk_count": document.chunk_count},
    )
    return document


@router.get("", response_model=DocumentPage)
def get_documents(
    tenant_id: UUID,
    context: TenantContextParam,
    session: SessionDependency,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> DocumentPage:
    if context.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return list_documents(session, context.tenant_id, limit=limit, offset=offset)


@router.get("/{document_id}", response_model=DocumentPublic)
def document_details(
    tenant_id: UUID, document_id: UUID, context: TenantContextParam, session: SessionDependency
) -> DocumentPublic:
    if context.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Document not found")
    try:
        return get_document(session, context.tenant_id, document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Document not found") from exc


@router.get("/{document_id}/status", response_model=DocumentStatusPublic)
def document_status(
    tenant_id: UUID, document_id: UUID, context: TenantContextParam, session: SessionDependency
) -> DocumentStatusPublic:
    if context.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Document not found")
    try:
        document = get_document(session, context.tenant_id, document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Document not found") from exc
    return DocumentStatusPublic(
        id=document.id, status=document.status, page_count=document.page_count,
        chunk_count=document.chunk_count, failure_detail=document.failure_detail,
    )
