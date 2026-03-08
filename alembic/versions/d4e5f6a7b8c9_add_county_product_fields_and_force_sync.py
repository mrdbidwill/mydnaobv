"""add_county_product_fields_and_force_sync

Revision ID: d4e5f6a7b8c9
Revises: c3f0a1b2c3d4
Create Date: 2026-03-07 16:20:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d4e5f6a7b8c9"
down_revision = "c3f0a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("observation_lists", sa.Column("product_type", sa.String(length=32), nullable=False, server_default="custom"))
    op.add_column("observation_lists", sa.Column("state_code", sa.String(length=2), nullable=True))
    op.add_column("observation_lists", sa.Column("county_name", sa.String(length=255), nullable=True))
    op.add_column("observation_lists", sa.Column("is_public_download", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.create_index(op.f("ix_observation_lists_product_type"), "observation_lists", ["product_type"], unique=False)
    op.create_index(op.f("ix_observation_lists_state_code"), "observation_lists", ["state_code"], unique=False)
    op.create_index(op.f("ix_observation_lists_county_name"), "observation_lists", ["county_name"], unique=False)
    op.create_index(op.f("ix_observation_lists_is_public_download"), "observation_lists", ["is_public_download"], unique=False)

    op.add_column("export_jobs", sa.Column("force_sync", sa.Boolean(), nullable=False, server_default=sa.text("false")))

    # Backfill probable county-product rows created before dedicated columns existed.
    op.execute(
        """
        UPDATE observation_lists
        SET
            product_type = 'county',
            is_public_download = true,
            county_name = NULLIF(trim(split_part(place_query, ',', 1)), ''),
            state_code = NULLIF(upper(trim(split_part(place_query, ', US,', 2))), '')
        WHERE
            COALESCE(inat_project_id, '') <> ''
            AND inat_user_id IS NULL
            AND COALESCE(inat_username, '') = ''
            AND COALESCE(place_query, '') ILIKE '%county%';
        """
    )

    op.alter_column("observation_lists", "product_type", server_default=None)
    op.alter_column("observation_lists", "is_public_download", server_default=None)
    op.alter_column("export_jobs", "force_sync", server_default=None)


def downgrade() -> None:
    op.drop_column("export_jobs", "force_sync")

    op.drop_index(op.f("ix_observation_lists_is_public_download"), table_name="observation_lists")
    op.drop_index(op.f("ix_observation_lists_county_name"), table_name="observation_lists")
    op.drop_index(op.f("ix_observation_lists_state_code"), table_name="observation_lists")
    op.drop_index(op.f("ix_observation_lists_product_type"), table_name="observation_lists")
    op.drop_column("observation_lists", "is_public_download")
    op.drop_column("observation_lists", "county_name")
    op.drop_column("observation_lists", "state_code")
    op.drop_column("observation_lists", "product_type")
