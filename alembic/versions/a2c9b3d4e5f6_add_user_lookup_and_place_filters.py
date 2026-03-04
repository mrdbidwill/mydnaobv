"""add_user_lookup_and_place_filters

Revision ID: a2c9b3d4e5f6
Revises: e1a3f8c42b7d
Create Date: 2026-03-04 11:45:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a2c9b3d4e5f6'
down_revision = 'e1a3f8c42b7d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column('observation_lists', 'inat_user_id',
               existing_type=sa.INTEGER(),
               nullable=True)
    op.add_column('observation_lists', sa.Column('inat_place_id', sa.Integer(), nullable=True))
    op.add_column('observation_lists', sa.Column('place_query', sa.String(length=255), nullable=True))
    op.create_index(op.f('ix_observation_lists_inat_place_id'), 'observation_lists', ['inat_place_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_observation_lists_inat_place_id'), table_name='observation_lists')
    op.drop_column('observation_lists', 'place_query')
    op.drop_column('observation_lists', 'inat_place_id')
    op.alter_column('observation_lists', 'inat_user_id',
               existing_type=sa.INTEGER(),
               nullable=False)
