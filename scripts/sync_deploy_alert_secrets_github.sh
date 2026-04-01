#!/usr/bin/env bash
set -euo pipefail

DEPLOY_ENV_FILE="${DEPLOY_ENV_FILE:-$HOME/.config/mydnaobv/deploy.env}"
REPO="${REPO:-}"

if [[ -f "${DEPLOY_ENV_FILE}" ]]; then
  # shellcheck source=/dev/null
  source "${DEPLOY_ENV_FILE}"
fi

PRIMARY="${POST_DEPLOY_ALERT_WEBHOOK_URL:-}"
FALLBACK="${POST_DEPLOY_ALERT_WEBHOOK_FALLBACK_URL:-}"

if ! command -v gh >/dev/null 2>&1; then
  echo "gh CLI is required." >&2
  exit 1
fi

if [[ -z "${PRIMARY}" ]]; then
  echo "POST_DEPLOY_ALERT_WEBHOOK_URL is required (env or ${DEPLOY_ENV_FILE})." >&2
  exit 1
fi

set_secret() {
  local name="$1"
  local value="$2"
  if [[ -n "${REPO}" ]]; then
    printf '%s' "${value}" | gh secret set "${name}" --repo "${REPO}" --body -
  else
    printf '%s' "${value}" | gh secret set "${name}" --body -
  fi
}

echo "Setting GitHub secret DEPLOY_ALERT_WEBHOOK_URL"
set_secret "DEPLOY_ALERT_WEBHOOK_URL" "${PRIMARY}"

if [[ -n "${FALLBACK}" ]]; then
  echo "Setting GitHub secret DEPLOY_ALERT_WEBHOOK_FALLBACK_URL"
  set_secret "DEPLOY_ALERT_WEBHOOK_FALLBACK_URL" "${FALLBACK}"
fi

echo "Done."
