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

args=()
if [[ -n "${REPO}" ]]; then
  args+=(--repo "${REPO}")
fi

echo "Setting GitHub secret DEPLOY_ALERT_WEBHOOK_URL"
printf '%s' "${PRIMARY}" | gh secret set DEPLOY_ALERT_WEBHOOK_URL "${args[@]}" --body -

if [[ -n "${FALLBACK}" ]]; then
  echo "Setting GitHub secret DEPLOY_ALERT_WEBHOOK_FALLBACK_URL"
  printf '%s' "${FALLBACK}" | gh secret set DEPLOY_ALERT_WEBHOOK_FALLBACK_URL "${args[@]}" --body -
fi

echo "Done."
