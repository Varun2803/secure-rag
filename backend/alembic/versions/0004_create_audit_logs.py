"""Create append-only audit logs.

Revision ID: 0004_create_audit_logs
Revises: 0003_create_documents
"""

from alembic import op
import sqlalchemy as sa

from app.audit.events import AuditEventType

revision = "0004_create_audit_logs"
down_revision = "0003_create_documents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    event_values = ", ".join(f"'{item.value}'" for item in AuditEventType)
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("actor_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=True),
        sa.Column("resource_id", sa.String(length=100), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column("request_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(f"event_type IN ({event_values})", name="ck_audit_logs_event_type"),
        sa.CheckConstraint("outcome IN ('SUCCESS', 'FAILURE', 'DENIED')", name="ck_audit_logs_outcome"),
        sa.CheckConstraint("length(action) BETWEEN 1 AND 80", name="ck_audit_logs_action_length"),
        sa.CheckConstraint("length(event_type) BETWEEN 1 AND 64", name="ck_audit_logs_event_type_length"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id", "event_type", "action", name="uq_audit_logs_request_event_action"),
    )
    op.create_index("ix_audit_logs_tenant_created_at", "audit_logs", ["tenant_id", "created_at", "id"])
    op.create_index("ix_audit_logs_actor_created_at", "audit_logs", ["actor_user_id", "created_at", "id"])
    op.create_index("ix_audit_logs_event_created_at", "audit_logs", ["event_type", "created_at", "id"])
    op.create_index("ix_audit_logs_resource", "audit_logs", ["resource_type", "resource_id"])
    op.execute(
        "CREATE FUNCTION reject_audit_log_mutation() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'audit_logs are append-only'; END; $$"
    )
    op.execute(
        "CREATE TRIGGER audit_logs_append_only BEFORE UPDATE OR DELETE ON audit_logs "
        "FOR EACH ROW EXECUTE FUNCTION reject_audit_log_mutation()"
    )
    op.execute(
        "CREATE TRIGGER audit_logs_no_truncate BEFORE TRUNCATE ON audit_logs "
        "FOR EACH STATEMENT EXECUTE FUNCTION reject_audit_log_mutation()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_logs_no_truncate ON audit_logs")
    op.execute("DROP TRIGGER IF EXISTS audit_logs_append_only ON audit_logs")
    op.execute("DROP FUNCTION IF EXISTS reject_audit_log_mutation()")
    op.drop_index("ix_audit_logs_resource", table_name="audit_logs")
    op.drop_index("ix_audit_logs_event_created_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_actor_created_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_tenant_created_at", table_name="audit_logs")
    op.drop_table("audit_logs")
