from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from app.documents.models import DocumentClassification
from app.permissions.models import PermissionSyncStatus
from app.tenants.models import TenantRole


class PermissionReplaceRequest(BaseModel):
    """Complete replacement ACL; omitted permissions are never implicitly retained."""

    model_config = ConfigDict(extra="forbid")

    classification: DocumentClassification
    is_public: bool
    allowed_roles: list[TenantRole]
    allowed_users: list[UUID]

    @field_validator("classification", mode="before")
    @classmethod
    def parse_classification(cls, value: object) -> DocumentClassification:
        if isinstance(value, str):
            try:
                return DocumentClassification[value.strip().upper()]
            except KeyError as exc:
                raise ValueError("Invalid document classification") from exc
        if isinstance(value, bool):
            raise ValueError("Invalid document classification")
        try:
            return DocumentClassification(value)
        except (ValueError, TypeError) as exc:
            raise ValueError("Invalid document classification") from exc

    @field_validator("allowed_roles")
    @classmethod
    def unique_roles(cls, value: list[TenantRole]) -> list[TenantRole]:
        if len(set(value)) != len(value):
            raise ValueError("Duplicate document roles are not allowed")
        return value

    @field_validator("allowed_users")
    @classmethod
    def unique_users(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("Duplicate document users are not allowed")
        return value


class DocumentPermissionsPublic(BaseModel):
    document_id: UUID
    tenant_id: UUID
    classification: str
    is_public: bool
    allowed_roles: list[TenantRole]
    allowed_users: list[UUID]
    permission_updated_at: datetime | None
    synchronization_status: PermissionSyncStatus
