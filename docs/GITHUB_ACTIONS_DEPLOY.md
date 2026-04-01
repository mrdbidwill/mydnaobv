# GitHub Actions Deploy Setup

Date: March 9, 2026

This repo includes `.github/workflows/deploy.yml` for production deploys.

## One-time setup

1. In GitHub repo settings, add these **Secrets and variables**:

Secrets:
- `DEPLOY_HOST` (example: `dna.mrdbid.com`)
- `DEPLOY_USER` (example: `mydnaobv`)
- `DEPLOY_SSH_KEY` (private SSH key text for `DEPLOY_USER`)
- `DEPLOY_ALERT_WEBHOOK_URL` (optional but recommended; primary deploy failure alert endpoint)
- `DEPLOY_ALERT_WEBHOOK_FALLBACK_URL` (optional; secondary alert endpoint)
  - for `DEPLOY_ALERT_FORMAT=ntfy`, these may be full URLs or bare topic names.
  - for other formats, use full `http://` or `https://` URLs (no whitespace/newlines).

Variables (optional, defaults shown):
- `DEPLOY_PORT=22`
- `DEPLOY_APP_DIR=/opt/mydnaobv/app`
- `DEPLOY_BRANCH=main`
- `DEPLOY_SERVICE_NAME=mydnaobv`
- `DEPLOY_HEALTHCHECK_URL=http://127.0.0.1/`
- `DEPLOY_HEALTHCHECK_HOST_HEADER=dna.mrdbid.com` (optional for nginx host-based routing)
- `DEPLOY_HEALTHCHECK_ATTEMPTS=6`
- `DEPLOY_HEALTHCHECK_RETRY_DELAY_SECONDS=5`
- `DEPLOY_SSH_ATTEMPTS=3`
- `DEPLOY_SSH_RETRY_DELAY_SECONDS=6`
- `SYSTEMCTL_USE_SUDO=1`
- `DEPLOY_RUN_POST_DEPLOY_SMOKE=1`
- `DEPLOY_SMOKE_BASE_URL=http://127.0.0.1`
- `DEPLOY_SMOKE_HOST_HEADER=dna.mrdbid.com`
- `DEPLOY_SMOKE_PATHS=` (optional explicit path list)
- `DEPLOY_SMOKE_MAX_PUBLIC_LINKS=3`
- `DEPLOY_ENABLE_AUTO_ROLLBACK=1`
- `DEPLOY_ROLLBACK_RUN_SMOKE=1`
- `DEPLOY_ROLLBACK_SMOKE_PATHS=` (optional explicit rollback path list)
- `DEPLOY_RUN_MIGRATION_COMPAT_CHECK=1`
- `DEPLOY_ALLOW_BREAKING_MIGRATIONS=0`
- `DEPLOY_ALERT_FORMAT=plain` (`ntfy`, `slack`, `discord` also supported)
- `DEPLOY_ALERT_NTFY_BASE_URL=https://ntfy.sh` (used when ntfy topic shorthand is provided)
- `DEPLOY_ALERT_TIMEOUT_SECONDS=10`
- `DEPLOY_ALERT_ON_SUCCESS=0`
- `DEPLOY_ALLOW_UNTRACKED=1`
- `DEPLOY_ALLOW_DIRTY=0`
- `DEPLOY_ENABLED=false` (set to `true` when you want auto deploy on push)

2. Ensure server user access:
- `DEPLOY_USER` can SSH to server.
- `DEPLOY_USER` can restart service (directly or via sudo, based on `SYSTEMCTL_USE_SUDO`).
- repository exists on server at `DEPLOY_APP_DIR`.

3. Ensure deploy script exists on server repo path after pull:
- `scripts/deploy_server.sh`

## Recommended rollout

1. Keep `DEPLOY_ENABLED=false`.
2. Run manual workflow once:
   - GitHub Actions -> `Deploy Production` -> `Run workflow`
3. Confirm deploy success on server logs/site.
4. Set `DEPLOY_ENABLED=true` to enable push-to-main auto deploy.

Deploy noise controls included:
- Auto deploy runs only on `main` pushes that touch deploy-relevant files.
- SSH deploy step retries automatically before failing.
- Health check retries automatically before failing.

## Failure quick-fixes

- `Permission denied (publickey,password)`:
  - wrong `DEPLOY_SSH_KEY` or key not in `~/.ssh/authorized_keys` for `DEPLOY_USER`.

- `Host key verification failed`:
  - workflow auto-adds host key with `ssh-keyscan`; verify `DEPLOY_HOST` and `DEPLOY_PORT`.

- Deployment skipped due invalid alert webhook config:
  - check `DEPLOY_ALERT_WEBHOOK_URL` / fallback secret format:
    - ntfy mode: full URL or bare topic, no spaces/newlines
    - non-ntfy mode: full `http://` or `https://` URL

- Health check `404` at `127.0.0.1`:
  - set `DEPLOY_HEALTHCHECK_HOST_HEADER` to the site hostname used by nginx server block.

- `systemctl restart ...` fails:
  - grant service restart permission or set `SYSTEMCTL_USE_SUDO=0` if not needed.

- `Repository has uncommitted changes; aborting`:
  - tracked file edits are blocked by default; set `DEPLOY_ALLOW_DIRTY=1` only for emergency/manual runs.
  - untracked files are allowed by default (`DEPLOY_ALLOW_UNTRACKED=1`).

- Migration failure:
  - inspect workflow logs at migration step (`alembic upgrade head`) and fix schema/env mismatch.
