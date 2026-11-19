from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, Index, JSON, String, Uuid, UniqueConstraint, event, func,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.audit.events import AuditEventType, AuditOutcome
from app.database.base import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        CheckConstraint(
            "event_type IN (" + ", ".join(f"'{item.value}'" for item in AuditEventType) + ")",
            name="ck_audit_logs_event_type",
        ),
        CheckConstraint("outcome IN ('SUCCESS', 'FAILURE', 'DENIED')", name="ck_audit_logs_outcome"),
        CheckConstraint("length(action) BETWEEN 1 AND 80", name="ck_audit_logs_action_length"),
        CheckConstraint("length(event_type) BETWEEN 1 AND 64", name="ck_audit_logs_event_type_length"),
        UniqueConstraint("request_id", "event_type", "action", name="uq_audit_logs_request_event_action"),
        Index("ix_audit_logs_tenant_created_at", "tenant_id", "created_at", "id"),
        Index("ix_audit_logs_actor_created_at", "actor_user_id", "created_at", "id"),
        Index("ix_audit_logs_event_created_at", "event_type", "created_at", "id"),
        Index("ix_audit_logs_resource", "resource_type", "resource_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=True,
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    metadata_json: Mapped[dict[str, object]] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), server_default=func.now(),
    )


class ImmutableAuditRecordError(RuntimeError):
    """Raised when application ORM code attempts to mutate a historical event."""


@event.listens_for(Session, "before_flush")
def prevent_audit_mutation(session: Session, _flush_context: object, _instances: object) -> None:
    if any(isinstance(item, AuditLog) for item in session.dirty) or any(
        isinstance(item, AuditLog) for item in session.deleted
    ):
        raise ImmutableAuditRecordError("Audit records are append-only")
