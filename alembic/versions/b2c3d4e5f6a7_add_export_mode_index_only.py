"""add_export_mode_index_only

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-22 00:01:00.000000

Add export_mode column to observation_lists.
- "full" (default): full pipeline with images, county guide PDF, and ZIP
- "index_only": observation index PDF + genera count TXT only; no images downloaded, no ZIP

Sets AMS Sequenced Specimens to index_only to avoid multi-GB ZIP artifacts.
"""
from alembic import op
import sqlalchemy as sa

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "observation_lists",
        sa.Column("export_mode", sa.String(32), nullable=False, server_default="full"),
    )

    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE observation_lists
            SET export_mode = 'index_only'
            WHERE product_type = 'project'
              AND inat_project_id = 'ams-sequenced-specimens'
            """
        )
    )


def downgrade() -> None:
    op.drop_column("observation_lists", "export_mode")
