# GitHub Actions Deploy Setup

Date: March 9, 2026

This repo includes `.github/workflows/deploy.yml` for production deploys.

## One-time setup

1. In GitHub repo settings, add these **Secrets and variables**:

Secrets:
- `DEPLOY_HOST` (example: `dna.mrdbid.com`)
- `DEPLOY_USER` (example: `mydnaobv`)
- `DEPLOY_SSH_KEY` (private SSH key text for `DEPLOY_USER`)

Variables (optional, defaults shown):
- `DEPLOY_PORT=22`
- `DEPLOY_APP_DIR=/opt/mydnaobv/app`
- `DEPLOY_BRANCH=main`
- `DEPLOY_SERVICE_NAME=mydnaobv`
- `DEPLOY_HEALTHCHECK_URL=http://127.0.0.1/`
- `SYSTEMCTL_USE_SUDO=1`
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

## Failure quick-fixes

- `Permission denied (publickey,password)`:
  - wrong `DEPLOY_SSH_KEY` or key not in `~/.ssh/authorized_keys` for `DEPLOY_USER`.

- `Host key verification failed`:
  - workflow auto-adds host key with `ssh-keyscan`; verify `DEPLOY_HOST` and `DEPLOY_PORT`.

- `systemctl restart ...` fails:
  - grant service restart permission or set `SYSTEMCTL_USE_SUDO=0` if not needed.

- `Repository has uncommitted changes; aborting`:
  - tracked file edits are blocked by default; set `DEPLOY_ALLOW_DIRTY=1` only for emergency/manual runs.
  - untracked files are allowed by default (`DEPLOY_ALLOW_UNTRACKED=1`).

- Migration failure:
  - inspect workflow logs at migration step (`alembic upgrade head`) and fix schema/env mismatch.
