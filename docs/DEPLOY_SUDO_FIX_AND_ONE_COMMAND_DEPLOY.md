# Deploy Runbook: Sudo Fix + One-Command Deploy

Date: 2026-03-14

## 1) Server-side sudo fix (run as `root` on Hostinger once)

```bash
SYSTEMCTL="$(command -v systemctl)"
cat > /etc/sudoers.d/mydnaobv-systemctl <<EOF
Defaults:mydnaobv !requiretty
mydnaobv ALL=(root) NOPASSWD: ${SYSTEMCTL} restart mydnaobv, ${SYSTEMCTL} status mydnaobv, ${SYSTEMCTL} is-active mydnaobv
EOF
chmod 440 /etc/sudoers.d/mydnaobv-systemctl
visudo -cf /etc/sudoers.d/mydnaobv-systemctl
```

If validation says `parsed OK`, the sudo fix is active.

## 2) One-command deploy (run locally from your Mac)

```bash
cd /Users/wrj/PycharmProjects/myDNAobv
EXPECTED_HOST_IP=85.31.233.192 \
HOST=dna.mrdbid.com \
USER_NAME=mydnaobv \
APP_DIR=/opt/mydnaobv/app \
BRANCH=main \
SERVICE_NAME=mydnaobv \
HEALTHCHECK_HOST_HEADER=dna.mrdbid.com \
./scripts/deploy_remote.sh
```

## 3) Quick verify (optional, run locally)

```bash
ssh -T mydnaobv@dna.mrdbid.com "cd /opt/mydnaobv/app && git rev-parse --short HEAD && systemctl is-active mydnaobv"
```

Expected:
- first line is latest commit hash
- second line is `active`

## 4) SSH/DNS guardrail reminder

`dna.mrdbid.com` must stay Cloudflare `DNS only` (gray cloud), not proxied, or SSH deploy will fail.

## 5) If GitHub/SSH deploy fails with `.git/objects` permission errors

Symptom:
- `error: insufficient permission for adding an object to repository database .git/objects`

Cause:
- mixed ownership in repo files (often from running git as `root` in app repo path).

Fix (run as `root` on server):

```bash
chown -R mydnaobv:mydnaobv /opt/mydnaobv/app
```

Prevention:
- avoid running `git fetch/pull` as `root` in `/opt/mydnaobv/app`.
- use deploy user (`mydnaobv`) for repo operations.
