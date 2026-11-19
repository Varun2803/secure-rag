from dataclasses import dataclass
from math import isfinite
from uuid import UUID

from qdrant_client import models

from app.tenants.dependencies import TenantContext
from app.tenants.models import TenantRole


@dataclass(frozen=True, slots=True)
class CandidateChunk:
    chunk_id: UUID
    document_id: UUID
    chunk_index: int
    page_numbers: tuple[int, ...]
    text: str
    score: float


def build_tenant_acl_filter(context: TenantContext) -> models.Filter:
    """Create server-side tenant and ACL candidate filters from trusted context."""
    if not isinstance(context.role, TenantRole):
        raise ValueError("Verified tenant role is invalid")
    return models.Filter(
        must=[
            models.FieldCondition(
                key="tenant_id",
                match=models.MatchValue(value=str(context.tenant_id)),
            )
        ],
        should=[
            models.FieldCondition(key="is_public", match=models.MatchValue(value=True)),
            models.FieldCondition(
                key="allowed_roles",
                match=models.MatchValue(value=context.role.value),
            ),
            models.FieldCondition(
                key="allowed_users",
                match=models.MatchValue(value=str(context.user_id)),
            ),
        ],
    )


def parse_candidate(point: object, expected_tenant_id: UUID) -> CandidateChunk | None:
    """Read only the expected response fields; reject malformed or cross-tenant hits."""
    payload = getattr(point, "payload", None)
    score = getattr(point, "score", None)
    if not isinstance(payload, dict):
        return None
    try:
        tenant_id = UUID(str(payload["tenant_id"]))
        document_id = UUID(str(payload["document_id"]))
        chunk_id = UUID(str(payload["chunk_id"]))
        point_id = UUID(str(getattr(point, "id")))
        chunk_index = payload["chunk_index"]
        page_numbers = payload.get("page_numbers")
        text = payload["text"]
    except (KeyError, ValueError, TypeError, AttributeError):
        return None
    if tenant_id != expected_tenant_id or chunk_id != point_id:
        return None
    if isinstance(chunk_index, bool) or not isinstance(chunk_index, int) or chunk_index < 0:
        return None
    if not isinstance(page_numbers, list) or not page_numbers:
        return None
    if any(isinstance(page, bool) or not isinstance(page, int) or page < 1 for page in page_numbers):
        return None
    if len(set(page_numbers)) != len(page_numbers):
        return None
    if not isinstance(text, str) or not text.strip():
        return None
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return None
    try:
        if not isfinite(score):
            return None
    except (TypeError, ValueError, OverflowError):
        return None
    return CandidateChunk(
        chunk_id=chunk_id,
        document_id=document_id,
        chunk_index=chunk_index,
        page_numbers=tuple(page_numbers),
        text=text,
        score=float(score),
    )


def can_access_document(document: object, context: TenantContext) -> bool:
    """Apply the baseline ACL policy using authoritative PostgreSQL fields."""
    if getattr(document, "tenant_id", None) != context.tenant_id:
        return False
    status = getattr(document, "status", None)
    if getattr(status, "value", status) != "COMPLETED":
        return False
    if getattr(document, "is_public", None) is True:
        return True

    roles = getattr(document, "allowed_roles", None)
    users = getattr(document, "allowed_users", None)
    if not isinstance(roles, list) or not isinstance(users, list):
        return False
    if any(not isinstance(role, str) or role not in {item.value for item in TenantRole} for role in roles):
        return False
    try:
        normalized_users = {str(UUID(str(user_id))) for user_id in users}
    except (ValueError, TypeError):
        return False
    return context.role.value in roles or str(context.user_id) in normalized_users
