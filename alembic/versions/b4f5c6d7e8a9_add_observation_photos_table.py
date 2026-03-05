"""add_observation_photos_table

Revision ID: b4f5c6d7e8a9
Revises: a2c9b3d4e5f6
Create Date: 2026-03-05 09:05:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b4f5c6d7e8a9'
down_revision = 'a2c9b3d4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'observation_photos',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('observation_id', sa.Integer(), nullable=False),
        sa.Column('inat_photo_id', sa.Integer(), nullable=True),
        sa.Column('photo_index', sa.Integer(), nullable=False),
        sa.Column('photo_url', sa.String(length=1024), nullable=False),
        sa.Column('photo_license_code', sa.String(length=64), nullable=True),
        sa.Column('photo_attribution', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['observation_id'], ['observations.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('observation_id', 'photo_index', name='uq_observation_photo_index'),
    )
    op.create_index(op.f('ix_observation_photos_inat_photo_id'), 'observation_photos', ['inat_photo_id'], unique=False)
    op.create_index(op.f('ix_observation_photos_observation_id'), 'observation_photos', ['observation_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_observation_photos_observation_id'), table_name='observation_photos')
    op.drop_index(op.f('ix_observation_photos_inat_photo_id'), table_name='observation_photos')
    op.drop_table('observation_photos')
