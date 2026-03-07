"""add_project_filter_to_lists

Revision ID: c3f0a1b2c3d4
Revises: b4f5c6d7e8a9
Create Date: 2026-03-07 13:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c3f0a1b2c3d4"
down_revision = "b4f5c6d7e8a9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("observation_lists", sa.Column("inat_project_id", sa.String(length=255), nullable=True))
    op.create_index(op.f("ix_observation_lists_inat_project_id"), "observation_lists", ["inat_project_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_observation_lists_inat_project_id"), table_name="observation_lists")
    op.drop_column("observation_lists", "inat_project_id")
