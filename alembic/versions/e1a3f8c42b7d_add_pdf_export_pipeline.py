"""add_pdf_export_pipeline

Revision ID: e1a3f8c42b7d
Revises: 0d5d939079ab
Create Date: 2026-03-04 10:20:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e1a3f8c42b7d'
down_revision = '0d5d939079ab'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('observations', sa.Column('photo_url', sa.String(length=1024), nullable=True))
    op.add_column('observations', sa.Column('photo_license_code', sa.String(length=64), nullable=True))
    op.add_column('observations', sa.Column('photo_attribution', sa.Text(), nullable=True))

    op.create_table(
        'export_jobs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('list_id', sa.Integer(), nullable=False),
        sa.Column('requested_by', sa.String(length=255), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('phase', sa.String(length=32), nullable=False),
        sa.Column('size_bucket', sa.String(length=8), nullable=True),
        sa.Column('total_items', sa.Integer(), nullable=False),
        sa.Column('eligible_items', sa.Integer(), nullable=False),
        sa.Column('downloaded_items', sa.Integer(), nullable=False),
        sa.Column('rendered_items', sa.Integer(), nullable=False),
        sa.Column('skipped_items', sa.Integer(), nullable=False),
        sa.Column('failed_items', sa.Integer(), nullable=False),
        sa.Column('api_requests', sa.Integer(), nullable=False),
        sa.Column('bytes_downloaded', sa.BigInteger(), nullable=False),
        sa.Column('part_size', sa.Integer(), nullable=False),
        sa.Column('message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.Column('next_run_at', sa.DateTime(), nullable=True),
        sa.Column('last_run_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['list_id'], ['observation_lists.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_export_jobs_list_id'), 'export_jobs', ['list_id'], unique=False)
    op.create_index(op.f('ix_export_jobs_next_run_at'), 'export_jobs', ['next_run_at'], unique=False)
    op.create_index(op.f('ix_export_jobs_size_bucket'), 'export_jobs', ['size_bucket'], unique=False)
    op.create_index(op.f('ix_export_jobs_status'), 'export_jobs', ['status'], unique=False)

    op.create_table(
        'export_items',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('job_id', sa.Integer(), nullable=False),
        sa.Column('observation_id', sa.Integer(), nullable=True),
        sa.Column('sequence', sa.Integer(), nullable=False),
        sa.Column('inat_observation_id', sa.Integer(), nullable=False),
        sa.Column('item_title', sa.String(length=255), nullable=True),
        sa.Column('observed_at', sa.DateTime(), nullable=True),
        sa.Column('inat_url', sa.String(length=512), nullable=False),
        sa.Column('image_url', sa.String(length=1024), nullable=True),
        sa.Column('image_license_code', sa.String(length=64), nullable=True),
        sa.Column('image_attribution', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('local_image_relpath', sa.String(length=1024), nullable=True),
        sa.Column('part_number', sa.Integer(), nullable=True),
        sa.Column('skip_reason', sa.Text(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['job_id'], ['export_jobs.id']),
        sa.ForeignKeyConstraint(['observation_id'], ['observations.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('job_id', 'sequence', name='uq_export_item_job_sequence'),
    )
    op.create_index(op.f('ix_export_items_inat_observation_id'), 'export_items', ['inat_observation_id'], unique=False)
    op.create_index(op.f('ix_export_items_job_id'), 'export_items', ['job_id'], unique=False)
    op.create_index(op.f('ix_export_items_observation_id'), 'export_items', ['observation_id'], unique=False)
    op.create_index(op.f('ix_export_items_sequence'), 'export_items', ['sequence'], unique=False)
    op.create_index(op.f('ix_export_items_status'), 'export_items', ['status'], unique=False)

    op.create_table(
        'export_artifacts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('job_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('part_number', sa.Integer(), nullable=True),
        sa.Column('relative_path', sa.String(length=1024), nullable=False),
        sa.Column('size_bytes', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['job_id'], ['export_jobs.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_export_artifacts_job_id'), 'export_artifacts', ['job_id'], unique=False)
    op.create_index(op.f('ix_export_artifacts_kind'), 'export_artifacts', ['kind'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_export_artifacts_kind'), table_name='export_artifacts')
    op.drop_index(op.f('ix_export_artifacts_job_id'), table_name='export_artifacts')
    op.drop_table('export_artifacts')

    op.drop_index(op.f('ix_export_items_status'), table_name='export_items')
    op.drop_index(op.f('ix_export_items_sequence'), table_name='export_items')
    op.drop_index(op.f('ix_export_items_observation_id'), table_name='export_items')
    op.drop_index(op.f('ix_export_items_job_id'), table_name='export_items')
    op.drop_index(op.f('ix_export_items_inat_observation_id'), table_name='export_items')
    op.drop_table('export_items')

    op.drop_index(op.f('ix_export_jobs_status'), table_name='export_jobs')
    op.drop_index(op.f('ix_export_jobs_size_bucket'), table_name='export_jobs')
    op.drop_index(op.f('ix_export_jobs_next_run_at'), table_name='export_jobs')
    op.drop_index(op.f('ix_export_jobs_list_id'), table_name='export_jobs')
    op.drop_table('export_jobs')

    op.drop_column('observations', 'photo_attribution')
    op.drop_column('observations', 'photo_license_code')
    op.drop_column('observations', 'photo_url')
