from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from app.audit.service import AuditPersistenceError, create_audit_event


def request_correlation_id(request: Request) -> UUID:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, UUID) else uuid4()


def record_request_event(
    request: Request,
    session: Session,
    **event: Any,
) -> None:
    """Persist a request event or fail the request with a sanitized 503."""
    try:
        create_audit_event(session, request_id=request_correlation_id(request), **event)
    except AuditPersistenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Audit service unavailable",
        ) from exc
