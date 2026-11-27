"""Idea owners can ask colleagues to endorse an idea.

Revision ID: 20261127_0037
Revises: 20261127_0036
Create Date: 2026-11-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261127_0037"
down_revision: str | None = "20261127_0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "idea_endorsements",
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
            "endorser_user_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "requested_by_user_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="requested"),
        sa.Column("message", sa.String(500), nullable=False, server_default=""),
        sa.Column("comment", sa.String(600), nullable=False, server_default=""),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("idea_id", "endorser_user_id", name="uq_idea_endorsements_endorser"),
        sa.CheckConstraint(
            "status IN ('requested', 'endorsed', 'declined')", name="ck_idea_endorsements_status"
        ),
    )
    op.create_index("ix_idea_endorsements_tenant_id", "idea_endorsements", ["tenant_id"])
    op.create_index(
        "ix_idea_endorsements_tenant_idea", "idea_endorsements", ["tenant_id", "idea_id", "status"]
    )
    op.create_index(
        "ix_idea_endorsements_tenant_endorser",
        "idea_endorsements",
        ["tenant_id", "endorser_user_id", "status"],
    )
    op.execute("ALTER TABLE idea_endorsements ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE idea_endorsements FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY idea_endorsements_tenant_isolation ON idea_endorsements
        USING (tenant_id = app_current_tenant_id())
        WITH CHECK (tenant_id = app_current_tenant_id())
        """
    )


def downgrade() -> None:
    op.drop_table("idea_endorsements")
