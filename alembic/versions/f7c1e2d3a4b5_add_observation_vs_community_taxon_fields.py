"""add_observation_vs_community_taxon_fields

Revision ID: f7c1e2d3a4b5
Revises: d4e5f6a7b8c9
Create Date: 2026-03-26 11:45:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f7c1e2d3a4b5"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("observations", sa.Column("observation_taxon_id", sa.Integer(), nullable=True))
    op.add_column("observations", sa.Column("observation_taxon_name", sa.String(length=255), nullable=True))
    op.add_column("observations", sa.Column("observation_taxon_rank", sa.String(length=64), nullable=True))
    op.add_column("observations", sa.Column("community_taxon_id", sa.Integer(), nullable=True))
    op.add_column("observations", sa.Column("community_taxon_name", sa.String(length=255), nullable=True))
    op.add_column("observations", sa.Column("community_taxon_rank", sa.String(length=64), nullable=True))
    op.create_index(op.f("ix_observations_observation_taxon_id"), "observations", ["observation_taxon_id"], unique=False)
    op.create_index(op.f("ix_observations_community_taxon_id"), "observations", ["community_taxon_id"], unique=False)

    op.execute(
        """
        UPDATE observations
        SET
            observation_taxon_name = COALESCE(NULLIF(scientific_name, ''), NULLIF(species_guess, ''), NULLIF(taxon_name, '')),
            community_taxon_name = COALESCE(NULLIF(taxon_name, ''), NULLIF(scientific_name, ''), NULLIF(species_guess, ''))
        """
    )

    op.add_column("export_items", sa.Column("observation_taxon_name", sa.String(length=255), nullable=True))
    op.add_column("export_items", sa.Column("community_taxon_name", sa.String(length=255), nullable=True))
    op.execute(
        """
        UPDATE export_items
        SET observation_taxon_name = COALESCE(NULLIF(item_title, ''), observation_taxon_name)
        """
    )


def downgrade() -> None:
    op.drop_column("export_items", "community_taxon_name")
    op.drop_column("export_items", "observation_taxon_name")

    op.drop_index(op.f("ix_observations_community_taxon_id"), table_name="observations")
    op.drop_index(op.f("ix_observations_observation_taxon_id"), table_name="observations")
    op.drop_column("observations", "community_taxon_rank")
    op.drop_column("observations", "community_taxon_name")
    op.drop_column("observations", "community_taxon_id")
    op.drop_column("observations", "observation_taxon_rank")
    op.drop_column("observations", "observation_taxon_name")
    op.drop_column("observations", "observation_taxon_id")
