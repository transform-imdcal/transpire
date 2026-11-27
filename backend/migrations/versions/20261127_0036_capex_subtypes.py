"""CAPEX project sub-categories become a workspace register, seeded from the URS.

Process areas are no longer offered in the product; their rows are kept for history.

Revision ID: 20261127_0036
Revises: 20261127_0035
Create Date: 2026-11-27
"""

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261127_0036"
down_revision: str | None = "20261127_0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CAPEX = (
    "Software Installation / Upgradation",
    "Digitalization & Automation",
    "Capacity Addition",
    "Equipment Installation",
    "Equipment Replacement",
    "Manufacturing Automation",
    "Facility Expansion",
    "Utility Upgradation",
    "Infrastructure Improvement",
    "EHS Compliance Upgrade",
    "Energy Efficiency Project",
    "New Manufacturing Line",
    "Laboratory Upgradation",
    "Warehouse Expansion",
    "Building & Civil Works",
    "IT Infrastructure Upgrade",
)


def _code(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")[:80]


def upgrade() -> None:
    for index, name in enumerate(CAPEX):
        op.get_bind().execute(
            sa.text(
                "INSERT INTO configuration_items "
                "(id, tenant_id, kind, code, name, is_active, sort_order, metadata) "
                "SELECT gen_random_uuid(), id, 'capex_subtype', :code, :name, true, :sort_order, "
                "jsonb_build_object('source', 'URS') FROM tenants "
                "ON CONFLICT (tenant_id, kind, code) DO NOTHING"
            ),
            {"code": _code(name), "name": name, "sort_order": index},
        )


def downgrade() -> None:
    op.execute("DELETE FROM configuration_items WHERE kind = 'capex_subtype'")
