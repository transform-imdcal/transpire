"""HR directory imports wait for an administrator to verify them before anyone is invited.

Revision ID: 20261125_0034
Revises: 20261119_0033
Create Date: 2026-11-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261125_0034"
down_revision: str | None = "20261119_0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # pending: imported from HR, not yet verified, nobody invited.
    # approved: verified by an administrator (or already had an account); invitation sent.
    # excluded: an administrator decided this employee should not get access.
    op.add_column(
        "directory_employees",
        sa.Column("access_review", sa.String(16), nullable=False, server_default="pending"),
    )
    op.add_column(
        "directory_employees",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "directory_employees",
        sa.Column(
            "reviewed_by_user_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    # Employees already linked to an account were invited by earlier versions: keep them.
    op.execute(
        "UPDATE directory_employees SET access_review = 'approved' WHERE user_id IS NOT NULL"
    )
    op.create_index(
        "ix_directory_employees_tenant_review",
        "directory_employees",
        ["tenant_id", "access_review"],
    )


def downgrade() -> None:
    op.drop_index("ix_directory_employees_tenant_review", table_name="directory_employees")
    op.drop_column("directory_employees", "reviewed_by_user_id")
    op.drop_column("directory_employees", "reviewed_at")
    op.drop_column("directory_employees", "access_review")
