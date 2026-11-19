from typing import Annotated
from time import perf_counter
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.audit.dependencies import record_request_event
from app.audit.events import AuditEventType, AuditOutcome
from app.database.postgres import get_db
from app.generation.schemas import GenerationRequest, GenerationResponse
from app.generation.service import GenerationUnavailableError, generate_answer
from app.retrieval.service import RetrievalUnavailableError
from app.tenants.dependencies import TenantContextDependency

router = APIRouter(prefix="/tenants/{tenant_id}/generation", tags=["generation"])
SessionDependency = Annotated[Session, Depends(get_db)]


@router.post("/answer", response_model=GenerationResponse)
def answer(
    tenant_id: UUID,
    request: GenerationRequest,
    context: TenantContextDependency,
    session: SessionDependency,
    request_context: Request,
) -> GenerationResponse:
    if tenant_id != context.tenant_id:
        record_request_event(
            request_context, session, event_type=AuditEventType.GENERATION_ACCESS_DENIED,
            action="generation.answer", outcome=AuditOutcome.DENIED,
            tenant_id=context.tenant_id, actor_user_id=context.user_id,
            resource_type="tenant", resource_id=tenant_id, reason_code="TENANT_MISMATCH",
        )
        raise HTTPException(status_code=404, detail="Tenant not found")
    record_request_event(
        request_context, session, event_type=AuditEventType.GENERATION_REQUEST,
        action="generation.answer", outcome=AuditOutcome.SUCCESS,
        tenant_id=context.tenant_id, actor_user_id=context.user_id,
        resource_type="tenant", resource_id=context.tenant_id,
        metadata={"top_k": request.top_k},
    )
    started = perf_counter()
    try:
        result = generate_answer(session, context=context, request=request)
    except (GenerationUnavailableError, RetrievalUnavailableError) as exc:
        session.rollback()
        record_request_event(
            request_context, session, event_type=AuditEventType.GENERATION_FAILURE,
            action="generation.answer", outcome=AuditOutcome.FAILURE,
            tenant_id=context.tenant_id, actor_user_id=context.user_id,
            reason_code="GENERATION_UNAVAILABLE",
            metadata={"top_k": request.top_k, "status_category": "UNAVAILABLE",
                      "duration_ms": int((perf_counter() - started) * 1000)},
        )
        raise HTTPException(status_code=503, detail="Generation service unavailable") from exc
    except Exception as exc:
        session.rollback()
        record_request_event(
            request_context, session, event_type=AuditEventType.GENERATION_FAILURE,
            action="generation.answer", outcome=AuditOutcome.FAILURE,
            tenant_id=context.tenant_id, actor_user_id=context.user_id,
            reason_code="INTERNAL_ERROR",
            metadata={"top_k": request.top_k, "duration_ms": int((perf_counter() - started) * 1000)},
        )
        raise HTTPException(status_code=500, detail="Internal server error") from exc
    event_type = AuditEventType.GENERATION_SUCCESS if result.grounded else AuditEventType.GENERATION_INSUFFICIENT_CONTEXT
    record_request_event(
        request_context, session, event_type=event_type,
        action="generation.answer", outcome=AuditOutcome.SUCCESS,
        tenant_id=context.tenant_id, actor_user_id=context.user_id,
        resource_type="tenant", resource_id=context.tenant_id,
        metadata={"top_k": request.top_k, "citation_count": len(result.citations),
                  "grounded": result.grounded,
                  "duration_ms": int((perf_counter() - started) * 1000)},
    )
    return result
