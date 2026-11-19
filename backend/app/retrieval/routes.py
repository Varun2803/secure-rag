from typing import Annotated
from time import perf_counter
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.audit.dependencies import record_request_event
from app.audit.events import AuditEventType, AuditOutcome
from app.database.postgres import get_db
from app.retrieval.schemas import RetrievalSearchRequest, RetrievalSearchResponse
from app.retrieval.service import RetrievalUnavailableError, search_documents
from app.tenants.dependencies import TenantContextDependency

router = APIRouter(prefix="/tenants/{tenant_id}/retrieval", tags=["retrieval"])
SessionDependency = Annotated[Session, Depends(get_db)]


@router.post("/search", response_model=RetrievalSearchResponse)
def retrieve(
    tenant_id: UUID,
    request: RetrievalSearchRequest,
    context: TenantContextDependency,
    session: SessionDependency,
    request_context: Request,
) -> RetrievalSearchResponse:
    if tenant_id != context.tenant_id:
        record_request_event(
            request_context, session, event_type=AuditEventType.RETRIEVAL_ACCESS_DENIED,
            action="retrieval.search", outcome=AuditOutcome.DENIED,
            tenant_id=context.tenant_id, actor_user_id=context.user_id,
            resource_type="tenant", resource_id=tenant_id, reason_code="TENANT_MISMATCH",
        )
        raise HTTPException(status_code=404, detail="Tenant not found")
    record_request_event(
        request_context, session, event_type=AuditEventType.RETRIEVAL_REQUEST,
        action="retrieval.search", outcome=AuditOutcome.SUCCESS,
        tenant_id=context.tenant_id, actor_user_id=context.user_id,
        resource_type="tenant", resource_id=context.tenant_id,
        metadata={"top_k": request.top_k},
    )
    started = perf_counter()
    try:
        result = search_documents(session, context=context, request=request)
    except RetrievalUnavailableError as exc:
        session.rollback()
        record_request_event(
            request_context, session, event_type=AuditEventType.RETRIEVAL_FAILURE,
            action="retrieval.search", outcome=AuditOutcome.FAILURE,
            tenant_id=context.tenant_id, actor_user_id=context.user_id,
            reason_code="RETRIEVAL_UNAVAILABLE",
            metadata={"top_k": request.top_k, "status_category": "UNAVAILABLE",
                      "duration_ms": int((perf_counter() - started) * 1000)},
        )
        raise HTTPException(status_code=503, detail="Retrieval service unavailable") from exc
    except Exception as exc:
        session.rollback()
        record_request_event(
            request_context, session, event_type=AuditEventType.RETRIEVAL_FAILURE,
            action="retrieval.search", outcome=AuditOutcome.FAILURE,
            tenant_id=context.tenant_id, actor_user_id=context.user_id,
            reason_code="INTERNAL_ERROR",
            metadata={"top_k": request.top_k, "status_category": "INTERNAL_ERROR",
                      "duration_ms": int((perf_counter() - started) * 1000)},
        )
        raise HTTPException(status_code=500, detail="Internal server error") from exc
    event_type = AuditEventType.RETRIEVAL_SUCCESS if result.result_count else AuditEventType.RETRIEVAL_EMPTY
    record_request_event(
        request_context, session, event_type=event_type,
        action="retrieval.search", outcome=AuditOutcome.SUCCESS,
        tenant_id=context.tenant_id, actor_user_id=context.user_id,
        resource_type="tenant", resource_id=context.tenant_id,
        metadata={"top_k": request.top_k, "result_count": result.result_count,
                  "duration_ms": int((perf_counter() - started) * 1000)},
    )
    return result
