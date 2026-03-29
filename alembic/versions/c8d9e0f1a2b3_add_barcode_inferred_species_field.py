"""add_barcode_inferred_species_field

Revision ID: c8d9e0f1a2b3
Revises: a9d8c7b6e5f4
Create Date: 2026-03-29 13:35:00.000000
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c8d9e0f1a2b3"
down_revision = "a9d8c7b6e5f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("observations", sa.Column("barcode_inferred_species_or_name", sa.Text(), nullable=True))
    op.add_column("export_items", sa.Column("barcode_inferred_species_or_name", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("export_items", "barcode_inferred_species_or_name")
    op.drop_column("observations", "barcode_inferred_species_or_name")
