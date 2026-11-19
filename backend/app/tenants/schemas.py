import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.tenants.models import TenantRole
from app.users.schemas import UserPublic

_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class TenantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=4000)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Tenant name must not be blank")
        return value

    @field_validator("slug")
    @classmethod
    def normalize_and_validate_slug(cls, value: str) -> str:
        normalized = "-".join(value.strip().casefold().replace("_", " ").split())
        if not _SLUG_PATTERN.fullmatch(normalized):
            raise ValueError("Slug may contain only letters, numbers, and hyphens")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class TenantUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("Tenant name cannot be null")
        value = value.strip()
        if not value:
            raise ValueError("Tenant name must not be blank")
        return value

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @model_validator(mode="after")
    def require_change(self) -> "TenantUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one tenant field must be supplied")
        return self


class TenantPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str
    description: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class TenantListItem(BaseModel):
    tenant: TenantPublic
    role: TenantRole
    joined_at: datetime


class TenantMembershipCreate(BaseModel):
    email: EmailStr
    role: TenantRole = TenantRole.EMPLOYEE

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: object) -> object:
        return value.strip().casefold() if isinstance(value, str) else value


class TenantMembershipUpdate(BaseModel):
    role: TenantRole | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def require_change(self) -> "TenantMembershipUpdate":
        if self.role is None and self.is_active is None:
            raise ValueError("At least one membership field must be supplied")
        return self


class TenantMembershipPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    user_id: UUID
    role: TenantRole
    is_active: bool
    joined_at: datetime
    created_at: datetime
    user: UserPublic
