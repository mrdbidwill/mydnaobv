"""add_catalog_has_dna_its_flag

Revision ID: f2a4c6d8e9b1
Revises: c8d9e0f1a2b3
Create Date: 2026-03-29 15:40:00.000000
"""

import json
import os

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f2a4c6d8e9b1"
down_revision = "c8d9e0f1a2b3"
branch_labels = None
depends_on = None


def _payload_has_dna_its(raw_payload: str | None, field_id: str) -> bool:
    if not raw_payload:
        return False
    try:
        payload = json.loads(raw_payload)
    except Exception:
        return False

    for key in ("ofvs", "observation_field_values"):
        values = payload.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            obs_field = item.get("observation_field")
            obs_field_id = obs_field.get("id") if isinstance(obs_field, dict) else None
            observed_field_id = item.get("observation_field_id") or item.get("field_id") or obs_field_id
            if str(observed_field_id) != field_id:
                continue
            if str(item.get("value") or "").strip():
                return True
    return False


def upgrade() -> None:
    op.add_column(
        "catalog_observations",
        sa.Column("has_dna_its", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_index(op.f("ix_catalog_observations_has_dna_its"), "catalog_observations", ["has_dna_its"], unique=False)

    bind = op.get_bind()
    field_id = (os.getenv("INAT_DNA_FIELD_ID") or "2330").strip() or "2330"

    result = bind.execute(sa.text("SELECT id, raw_payload FROM catalog_observations"))
    update_stmt = sa.text("UPDATE catalog_observations SET has_dna_its = true WHERE id = :row_id")

    while True:
        rows = result.fetchmany(500)
        if not rows:
            break

        updates: list[dict[str, int]] = []
        for row in rows:
            row_id = row[0]
            raw_payload = row[1]
            if _payload_has_dna_its(raw_payload, field_id):
                updates.append({"row_id": row_id})

        if updates:
            bind.execute(update_stmt, updates)

    op.alter_column("catalog_observations", "has_dna_its", server_default=None)


def downgrade() -> None:
    op.drop_index(op.f("ix_catalog_observations_has_dna_its"), table_name="catalog_observations")
    op.drop_column("catalog_observations", "has_dna_its")
