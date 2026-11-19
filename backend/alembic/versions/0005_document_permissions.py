"""Add tenant-scoped permission outbox and permission timestamps.

Revision ID: 0005_document_permissions
Revises: 0004_create_audit_logs
"""

from alembic import op
import sqlalchemy as sa

from app.audit.events import AuditEventType

revision = "0005_document_permissions"
down_revision = "0004_create_audit_logs"
branch_labels = None
depends_on = None

def _event_check_values() -> str:
    return ", ".join(f"'{item.value}'" for item in AuditEventType)


def upgrade() -> None:
    op.add_column("documents", sa.Column("permission_updated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "document_permission_sync",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("document_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="PENDING", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('PENDING', 'PROCESSING', 'COMPLETED')", name="ck_permission_sync_status"),
        sa.CheckConstraint("attempts >= 0", name="ck_permission_sync_attempts_nonnegative"),
        sa.CheckConstraint("revision > 0", name="ck_permission_sync_revision_positive"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "document_id", name="uq_permission_sync_tenant_document"),
    )
    op.create_index("ix_permission_sync_status_updated", "document_permission_sync", ["status", "updated_at"])
    op.drop_constraint("ck_audit_logs_event_type", "audit_logs", type_="check")
    op.create_check_constraint(
        "ck_audit_logs_event_type", "audit_logs", f"event_type IN ({_event_check_values()})",
    )


def downgrade() -> None:
    # Keep the expanded event-type check after downgrade so append-only audit
    # history written while Phase 8 was deployed remains valid and readable.
    op.drop_index("ix_permission_sync_status_updated", table_name="document_permission_sync")
    op.drop_table("document_permission_sync")
    op.drop_column("documents", "permission_updated_at")
