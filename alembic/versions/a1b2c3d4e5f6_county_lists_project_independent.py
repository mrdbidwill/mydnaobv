"""county_lists_project_independent

Revision ID: a1b2c3d4e5f6
Revises: f7c1e2d3a4b5
Create Date: 2026-06-22 00:00:00.000000

Remove inat_project_id from county observation lists and clean up their titles.
County syncs already query all AMS projects via INAT_COUNTY_PROJECT_IDS config;
the stored project association was cosmetic and misleading.
"""
from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "f7c1e2d3a4b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # Strip the " — <project_slug>" suffix from county list titles.
    conn.execute(
        sa.text(
            """
            UPDATE observation_lists
            SET title = regexp_replace(title, ' — .+$', '')
            WHERE product_type = 'county'
              AND title LIKE '% — %'
            """
        )
    )

    # Clear the project association — counties are project-independent.
    conn.execute(
        sa.text(
            """
            UPDATE observation_lists
            SET inat_project_id = NULL
            WHERE product_type = 'county'
            """
        )
    )

    # Add a partial unique index to prevent duplicate county rows in future seeding.
    op.create_index(
        "ix_observation_lists_county_unique",
        "observation_lists",
        ["state_code", "county_name"],
        unique=True,
        postgresql_where=sa.text("product_type = 'county'"),
    )


def downgrade() -> None:
    op.drop_index("ix_observation_lists_county_unique", table_name="observation_lists")
    # Titles and inat_project_id are not restored — downgrade leaves data as-is.
