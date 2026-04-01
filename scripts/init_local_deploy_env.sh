#!/usr/bin/env bash
set -euo pipefail

DEPLOY_ENV_FILE="${DEPLOY_ENV_FILE:-$HOME/.config/mydnaobv/deploy.env}"

mkdir -p "$(dirname "${DEPLOY_ENV_FILE}")"
chmod 700 "$(dirname "${DEPLOY_ENV_FILE}")"

if [[ -f "${DEPLOY_ENV_FILE}" ]]; then
  echo "Deploy env file already exists: ${DEPLOY_ENV_FILE}"
  exit 0
fi

cat > "${DEPLOY_ENV_FILE}" <<'EOF'
# myDNAobv deploy secrets/defaults (local only; never commit)
POST_DEPLOY_ALERT_WEBHOOK_URL=
POST_DEPLOY_ALERT_WEBHOOK_FALLBACK_URL=
DEPLOY_ALERT_FORMAT=ntfy
ENABLE_AUTO_ROLLBACK=1
RUN_POST_DEPLOY_SMOKE=1
RUN_MIGRATION_COMPAT_CHECK=1
ROLLBACK_RUN_SMOKE=1
EOF

chmod 600 "${DEPLOY_ENV_FILE}"
echo "Created ${DEPLOY_ENV_FILE} (permissions 600). Fill in alert webhook URLs (or ntfy topic names when DEPLOY_ALERT_FORMAT=ntfy)."
