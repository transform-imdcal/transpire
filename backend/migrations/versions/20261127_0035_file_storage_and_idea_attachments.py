"""File storage records, idea attachments and the workspace attachment policy.

Revision ID: 20261127_0035
Revises: 20261125_0034
Create Date: 2026-11-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261127_0035"
down_revision: str | None = "20261125_0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = ("stored_files", "idea_attachments", "attachment_policies")


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "stored_files",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("storage_key", sa.String(300), nullable=False),
        sa.Column("preview_key", sa.String(300), nullable=True),
        sa.Column("original_name", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column(
            "uploaded_by_user_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("storage_key", name="uq_stored_files_storage_key"),
        sa.CheckConstraint("status IN ('pending', 'ready')", name="ck_stored_files_status"),
        sa.CheckConstraint("size_bytes > 0", name="ck_stored_files_size"),
    )
    op.create_index("ix_stored_files_tenant_id", "stored_files", ["tenant_id"])
    op.create_index(
        "ix_stored_files_tenant_status_created",
        "stored_files",
        ["tenant_id", "status", "created_at"],
    )

    op.create_table(
        "idea_attachments",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "idea_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("ideas.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "file_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("stored_files.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        *_timestamps(),
        sa.UniqueConstraint("file_id", name="uq_idea_attachments_file"),
    )
    op.create_index("ix_idea_attachments_tenant_id", "idea_attachments", ["tenant_id"])
    op.create_index(
        "ix_idea_attachments_tenant_idea", "idea_attachments", ["tenant_id", "idea_id", "position"]
    )

    op.create_table(
        "attachment_policies",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("max_files", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("max_file_mb", sa.Integer(), nullable=False, server_default="25"),
        sa.Column("allow_pdf", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("allow_images", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", name="uq_attachment_policies_tenant"),
        sa.CheckConstraint("max_files >= 0", name="ck_attachment_policies_max_files"),
        sa.CheckConstraint("max_file_mb >= 1", name="ck_attachment_policies_max_file_mb"),
    )
    op.create_index("ix_attachment_policies_tenant_id", "attachment_policies", ["tenant_id"])

    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
            USING (tenant_id = app_current_tenant_id())
            WITH CHECK (tenant_id = app_current_tenant_id())
            """
        )


def downgrade() -> None:
    op.drop_table("attachment_policies")
    op.drop_table("idea_attachments")
    op.drop_table("stored_files")
