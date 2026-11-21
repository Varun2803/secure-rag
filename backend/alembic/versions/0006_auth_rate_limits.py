"""Add shared auth rate-limit buckets.

Revision ID: 0006_auth_rate_limits
Revises: 0005_document_permissions
"""

from alembic import op
import sqlalchemy as sa

revision = "0006_auth_rate_limits"
down_revision = "0005_document_permissions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_rate_limit_buckets",
        sa.Column("bucket_key", sa.String(length=64), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.CheckConstraint("request_count > 0", name="ck_auth_rate_limit_count_positive"),
        sa.PrimaryKeyConstraint("bucket_key"),
    )
    op.create_index(
        "ix_auth_rate_limit_window_started", "auth_rate_limit_buckets", ["window_started_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_auth_rate_limit_window_started", table_name="auth_rate_limit_buckets")
    op.drop_table("auth_rate_limit_buckets")
