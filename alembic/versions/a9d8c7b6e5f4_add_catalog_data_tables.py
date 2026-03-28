"""add_catalog_data_tables

Revision ID: a9d8c7b6e5f4
Revises: f7c1e2d3a4b5
Create Date: 2026-03-28 07:15:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a9d8c7b6e5f4"
down_revision = "f7c1e2d3a4b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "catalog_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.String(length=255), nullable=False),
        sa.Column("project_numeric_id", sa.Integer(), nullable=True),
        sa.Column("project_title", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_sync_at", sa.DateTime(), nullable=True),
        sa.Column("last_sync_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id"),
    )
    op.create_index(op.f("ix_catalog_sources_project_id"), "catalog_sources", ["project_id"], unique=True)
    op.create_index(op.f("ix_catalog_sources_project_numeric_id"), "catalog_sources", ["project_numeric_id"], unique=False)
    op.create_index(op.f("ix_catalog_sources_is_active"), "catalog_sources", ["is_active"], unique=False)

    op.create_table(
        "catalog_observations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("inat_observation_id", sa.Integer(), nullable=False),
        sa.Column("uri", sa.String(length=512), nullable=True),
        sa.Column("taxon_id", sa.Integer(), nullable=True),
        sa.Column("taxon_name", sa.String(length=255), nullable=True),
        sa.Column("taxon_rank", sa.String(length=64), nullable=True),
        sa.Column("community_taxon_id", sa.Integer(), nullable=True),
        sa.Column("community_taxon_name", sa.String(length=255), nullable=True),
        sa.Column("community_taxon_rank", sa.String(length=64), nullable=True),
        sa.Column("species_guess", sa.String(length=255), nullable=True),
        sa.Column("user_login", sa.String(length=255), nullable=True),
        sa.Column("quality_grade", sa.String(length=64), nullable=True),
        sa.Column("observed_on", sa.String(length=32), nullable=True),
        sa.Column("observed_on_date", sa.Date(), nullable=True),
        sa.Column("observed_at", sa.DateTime(), nullable=True),
        sa.Column("inat_created_at", sa.DateTime(), nullable=True),
        sa.Column("inat_updated_at", sa.DateTime(), nullable=True),
        sa.Column("place_guess", sa.String(length=255), nullable=True),
        sa.Column("location", sa.String(length=128), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("geoprivacy", sa.String(length=64), nullable=True),
        sa.Column("genus_key", sa.String(length=128), nullable=True),
        sa.Column("primary_photo_url", sa.String(length=1024), nullable=True),
        sa.Column("primary_photo_license_code", sa.String(length=64), nullable=True),
        sa.Column("primary_photo_attribution", sa.Text(), nullable=True),
        sa.Column("photo_count", sa.Integer(), nullable=False),
        sa.Column("raw_payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("inat_observation_id"),
    )
    op.create_index(op.f("ix_catalog_observations_inat_observation_id"), "catalog_observations", ["inat_observation_id"], unique=True)
    op.create_index(op.f("ix_catalog_observations_taxon_id"), "catalog_observations", ["taxon_id"], unique=False)
    op.create_index(op.f("ix_catalog_observations_taxon_name"), "catalog_observations", ["taxon_name"], unique=False)
    op.create_index(op.f("ix_catalog_observations_community_taxon_id"), "catalog_observations", ["community_taxon_id"], unique=False)
    op.create_index(op.f("ix_catalog_observations_community_taxon_name"), "catalog_observations", ["community_taxon_name"], unique=False)
    op.create_index(op.f("ix_catalog_observations_user_login"), "catalog_observations", ["user_login"], unique=False)
    op.create_index(op.f("ix_catalog_observations_quality_grade"), "catalog_observations", ["quality_grade"], unique=False)
    op.create_index(op.f("ix_catalog_observations_observed_on_date"), "catalog_observations", ["observed_on_date"], unique=False)
    op.create_index(op.f("ix_catalog_observations_inat_created_at"), "catalog_observations", ["inat_created_at"], unique=False)
    op.create_index(op.f("ix_catalog_observations_inat_updated_at"), "catalog_observations", ["inat_updated_at"], unique=False)
    op.create_index(op.f("ix_catalog_observations_place_guess"), "catalog_observations", ["place_guess"], unique=False)
    op.create_index(op.f("ix_catalog_observations_genus_key"), "catalog_observations", ["genus_key"], unique=False)

    op.create_table(
        "catalog_observation_projects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("observation_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["observation_id"], ["catalog_observations.id"]),
        sa.ForeignKeyConstraint(["source_id"], ["catalog_sources.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "observation_id", name="uq_catalog_source_observation"),
    )
    op.create_index(
        op.f("ix_catalog_observation_projects_source_id"),
        "catalog_observation_projects",
        ["source_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_catalog_observation_projects_observation_id"),
        "catalog_observation_projects",
        ["observation_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_catalog_observation_projects_observation_id"), table_name="catalog_observation_projects")
    op.drop_index(op.f("ix_catalog_observation_projects_source_id"), table_name="catalog_observation_projects")
    op.drop_table("catalog_observation_projects")

    op.drop_index(op.f("ix_catalog_observations_genus_key"), table_name="catalog_observations")
    op.drop_index(op.f("ix_catalog_observations_place_guess"), table_name="catalog_observations")
    op.drop_index(op.f("ix_catalog_observations_inat_updated_at"), table_name="catalog_observations")
    op.drop_index(op.f("ix_catalog_observations_inat_created_at"), table_name="catalog_observations")
    op.drop_index(op.f("ix_catalog_observations_observed_on_date"), table_name="catalog_observations")
    op.drop_index(op.f("ix_catalog_observations_quality_grade"), table_name="catalog_observations")
    op.drop_index(op.f("ix_catalog_observations_user_login"), table_name="catalog_observations")
    op.drop_index(op.f("ix_catalog_observations_community_taxon_name"), table_name="catalog_observations")
    op.drop_index(op.f("ix_catalog_observations_community_taxon_id"), table_name="catalog_observations")
    op.drop_index(op.f("ix_catalog_observations_taxon_name"), table_name="catalog_observations")
    op.drop_index(op.f("ix_catalog_observations_taxon_id"), table_name="catalog_observations")
    op.drop_index(op.f("ix_catalog_observations_inat_observation_id"), table_name="catalog_observations")
    op.drop_table("catalog_observations")

    op.drop_index(op.f("ix_catalog_sources_is_active"), table_name="catalog_sources")
    op.drop_index(op.f("ix_catalog_sources_project_numeric_id"), table_name="catalog_sources")
    op.drop_index(op.f("ix_catalog_sources_project_id"), table_name="catalog_sources")
    op.drop_table("catalog_sources")
