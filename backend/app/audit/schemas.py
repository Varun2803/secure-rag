from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AuditEventPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID | None
    actor_user_id: UUID | None
    event_type: str
    action: str
    resource_type: str | None
    resource_id: str | None
    outcome: str
    reason_code: str | None
    request_id: UUID | None
    metadata_json: dict[str, object]
    created_at: datetime


class AuditEventPage(BaseModel):
    items: list[AuditEventPublic]
    total: int
    limit: int
    offset: int
