#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/mydnaobv/app}"
RESTORE_DB="${DB_RESTORE_VERIFY_DATABASE:-mydnaobv_restore_verify}"

cd "${APP_DIR}"

db_line="$(grep '^DATABASE_URL=' .env | head -n1 || true)"
if [[ -z "${db_line}" ]]; then
  echo "[restore-verify] DATABASE_URL not found in ${APP_DIR}/.env" >&2
  exit 1
fi

db_url="${db_line#DATABASE_URL=}"
db_url="${db_url#\"}"
db_url="${db_url%\"}"

./scripts/verify_db_backup_restore.py --database-url "${db_url}" --restore-db "${RESTORE_DB}"
