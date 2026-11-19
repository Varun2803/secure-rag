from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.documents.models import DocumentStatus


class DocumentPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    tenant_id: UUID
    uploaded_by: UUID
    filename: str
    original_filename: str
    file_hash: str
    file_size: int
    content_type: str
    page_count: int
    chunk_count: int
    status: DocumentStatus
    classification: int
    is_public: bool
    allowed_roles: list[str]
    allowed_users: list[str]
    created_at: datetime
    updated_at: datetime
    processing_completed_at: datetime | None


class DocumentStatusPublic(BaseModel):
    id: UUID
    status: DocumentStatus
    page_count: int
    chunk_count: int
    failure_detail: str | None


class DocumentPage(BaseModel):
    items: list[DocumentPublic]
    total: int
    limit: int
    offset: int
