"""Create tenant-scoped document metadata.

Revision ID: 0003_create_documents
Revises: 0002_tenants_memberships
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_create_documents"
down_revision = "0002_tenants_memberships"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("uploaded_by", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("classification", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("allowed_roles", sa.JSON(), nullable=False),
        sa.Column("allowed_users", sa.JSON(), nullable=False),
        sa.Column("failure_detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("processing_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("file_size > 0", name="ck_documents_file_size_positive"),
        sa.CheckConstraint("page_count >= 0 AND chunk_count >= 0", name="ck_documents_counts_nonnegative"),
        sa.CheckConstraint("classification BETWEEN 0 AND 3", name="ck_documents_classification"),
        sa.CheckConstraint("status IN ('PENDING','PROCESSING','COMPLETED','FAILED')", name="ck_documents_status"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "file_hash", name="uq_documents_tenant_file_hash"),
        sa.UniqueConstraint("filename", name="uq_documents_filename"),
    )
    op.create_index("ix_documents_tenant_created_at", "documents", ["tenant_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_documents_tenant_created_at", table_name="documents")
    op.drop_table("documents")
