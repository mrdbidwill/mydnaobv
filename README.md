# myDNAobv

A lightweight, maintainable FastAPI app for county-level iNaturalist PDF products.
Admins seed/build county lists by state+project; public users browse/download finished county files.

Project continuity docs:
- `docs/PROJECT_MEMORY.md` (dated decision/history log for future sessions)
- `docs/KVM4_COUNTY_PIPELINE_ROADMAP.md` (staged plan for KVM4 + county-product pipeline)
- `docs/GITHUB_ACTIONS_DEPLOY.md` (GitHub Actions deploy setup + troubleshooting)
- `docs/R2_ADSENSE_TRANSITION_PLAN.md` (object-storage + monetization rollout plan)

## Current flow

- Public homepage `/` shows paginated county download catalog (finished files only).
  - Separate downloads: county guide file + DNA-confirmed observation index PDF.
  - Public rows show refresh recency and weekly refresh target.
- Admin page `/admin` controls:
  - seed counties by state+project
  - process state-wide rebuilds
  - per-county sync/rebuild/show-hide/delete actions
- Export queue/worker remains throttled to protect VPS and iNaturalist limits.
- Legacy `/exports` now redirects to `/admin`.

## Quick start

Prerequisites (local development):

- `python3` available on PATH (macOS/Linux often do not provide plain `python`)
- virtualenv support (`python3 -m venv`)
- `ripgrep` (`rg`) recommended for fast code search

1. Create a virtual environment and install deps:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

2. Configure environment:

```bash
cp .env.example .env
```

Do not `source .env` in your shell. `.env` values are app config, not shell-safe syntax.
Some values contain spaces and can break shell parsing.

Safe patterns:

```bash
# run app/tools with app-level .env loading
uvicorn app.main:app --reload
alembic upgrade head

# read individual values when needed
grep '^ADMIN_USERNAME=' .env
grep '^ADMIN_PASSWORD=' .env
```

3. Run database migrations (once configured):

```bash
alembic upgrade head
```

4. Start the app:

```bash
uvicorn app.main:app --reload
```

5. Run tests:

```bash
pytest -q
```

## Queued PDF exports (KVM staged profile)

This project now supports a modular, queue-based PDF export pipeline for offline use.

- Exports never run inline with the web request.
- A worker processes one job at a time in short slices (`--once`).
- Jobs are prioritized smallest to largest (`XS`, `S`, `M`, `L`).
- Large jobs are limited to an overnight window.
- If merge pressure is high, output degrades gracefully to split PDFs + ZIP.
- Every completed job includes `observations_index.pdf` for linked record review.
- Queue requests use a stale detector: no new job is created when list data has not changed since the latest completed export.
- Public list creation and browsing remain unchanged.
- Heavy export controls live on authenticated admin page: `/admin`.
- Optional publish mode copies finished files to external/static storage and exposes a public member page: `/downloads`.
- Optional mode: include multiple photos per observation with conservative KVM1 caps.
- County jobs with zero exportable image pages now complete with a placeholder county guide PDF instead of failing.
- Create/edit flow now includes a fast pre-check estimate, and list/export pages show synced-data ETA ranges before queueing.
- Export access supports:
  - `EXPORT_OPERATORS_JSON` (preferred, multiple operator accounts), or
  - `EXPORT_USERNAME` / `EXPORT_PASSWORD` (single account), or
  - admin credentials fallback if neither is configured.

Example multi-operator config:

```env
EXPORT_OPERATORS_JSON=[{"username":"ams_alice","password":"strong-pass-1"},{"username":"ams_bob","password":"strong-pass-2"}]
```

Recommended low-storage export mode:

```env
EXPORT_INCLUDE_ALL_PHOTOS=false
EXPORT_MAX_PHOTOS_PER_OBSERVATION=1
EXPORT_RETENTION_HOURS=48
```

Example published member downloads mode:

```env
EXPORT_PUBLISH_ENABLED=true
EXPORT_PUBLISH_DIR=/var/www/mydnaobv-downloads
EXPORT_PUBLISH_BASE_URL=https://downloads.example.org/mydnaobv
EXPORT_PUBLIC_DOWNLOADS_ENABLED=true
PUBLIC_REFRESH_INTERVAL_DAYS=7
PUBLIC_STATE_CODES=AL
```

Example Cloudflare R2 publish mode (S3-compatible backend):

```env
EXPORT_PUBLISH_ENABLED=true
EXPORT_PUBLISH_BACKEND=s3
EXPORT_PUBLISH_BUCKET=mydnaobv-downloads
EXPORT_PUBLISH_PREFIX=mydnaobv
EXPORT_PUBLISH_S3_ENDPOINT=https://<accountid>.r2.cloudflarestorage.com
EXPORT_PUBLISH_S3_REGION=auto
EXPORT_PUBLISH_S3_ACCESS_KEY_ID=<r2-access-key-id>
EXPORT_PUBLISH_S3_SECRET_ACCESS_KEY=<r2-secret-access-key>
EXPORT_PUBLISH_BASE_URL=https://downloads.dna.mrdbid.com/mydnaobv
EXPORT_PUBLIC_DOWNLOADS_ENABLED=true
```

AdSense public-page gating:

```env
ADSENSE_ENABLED=true
ADSENSE_CLIENT_ID=ca-pub-1234567890123456
# Optional fixed banner slot; leave empty for Auto Ads only.
ADSENSE_BANNER_SLOT=
```

- Ads are rendered on public template responses only (`/` in current app).
- Admin and authenticated pages do not include the AdSense script.

Recommended staged-throughput timing profile (keeps iNaturalist guardrails unchanged):

```env
EXPORT_RUN_TIMEOUT_SECONDS=90
EXPORT_XS_CADENCE_MINUTES=2
EXPORT_S_CADENCE_MINUTES=4
EXPORT_M_CADENCE_MINUTES=8
EXPORT_L_CADENCE_MINUTES=20
EXPORT_L_WINDOW_START_HOUR=0
EXPORT_L_WINDOW_END_HOUR=12
```

### Rights and license policy

- By default, only these photo licenses are exportable: `cc0`, `cc-by`, `cc-by-sa`, `cc-by-nc`, `cc-by-nc-sa`.
- Missing or restricted licenses are skipped unless explicitly allowed by configuration.
- Attribution and source link are printed on each PDF page.

### Required migration

Run migrations before using exports:

```bash
alembic upgrade head
```

### Worker and cron

Enable exports in `.env`:

```bash
ENABLE_PDF_EXPORTS=true
```

Run once manually:

```bash
python3 -m app.exports.worker --once
```

Cleanup expired artifacts:

```bash
python3 -m app.exports.worker --cleanup
```

Dry-run orphan export/publish directory cleanup (folders left behind with no DB rows):

```bash
python scripts/cleanup_orphan_exports.py
python scripts/cleanup_orphan_exports.py --apply
```

Suggested cron entries (staged throughput):

```cron
*/2 * * * * flock -n /var/lock/mydnaobv_export.lock timeout 120s nice -n 15 ionice -c2 -n7 /usr/bin/python3 -m app.exports.worker --once
17 3 * * * /usr/bin/python3 -m app.exports.worker --cleanup
```

Problem-observation reporting (for owner follow-up):

```bash
python scripts/export_problem_observations.py --state AL --days 30 --output reports/problem_observations_AL_latest.csv
```

This report lists failed/skipped items from the latest county job per list, including iNaturalist URLs, issue type, and observer field for manual outreach.

Latest failed job per county mode:

```bash
python scripts/export_problem_observations.py --mode latest_failed --state AL --days 30 --output reports/problem_observations_AL_latest_failed.csv
```

Unique user list for license outreach (from generated report CSV):

```bash
python scripts/export_license_issue_users.py --input reports/problem_observations_AL_latest.csv --output reports/license_issue_users_AL_latest.csv
```

Production deploy automation:

On the server host:

```bash
APP_DIR=/opt/mydnaobv/app BRANCH=main SERVICE_NAME=mydnaobv ./scripts/deploy_server.sh
```

From your local machine over SSH:

```bash
HOST=dna.mrdbid.com USER_NAME=mydnaobv APP_DIR=/opt/mydnaobv/app BRANCH=main SERVICE_NAME=mydnaobv ./scripts/deploy_remote.sh
```

Optional flags:
- `RUN_TESTS=1` to run tests during deploy
- `SYSTEMCTL_USE_SUDO=0` if service user can restart without sudo
- `HEALTHCHECK_URL=http://127.0.0.1/` to override health endpoint
- `HEALTHCHECK_HOST_HEADER=dna.mrdbid.com` when local vhost routing needs a Host header
- `ALLOW_UNTRACKED=1` allows local untracked files on server (default)
- `ALLOW_DIRTY=1` to bypass clean-worktree protection (not recommended)

GitHub Actions deploy:

- Workflow file: `.github/workflows/deploy.yml`
- Safe by default:
  - push-to-main deploy is skipped unless repository variable `DEPLOY_ENABLED=true`
  - missing deploy secrets cause a clean "skipped" run (not a failure)
- Required repository secrets:
  - `DEPLOY_HOST` (example: `dna.mrdbid.com`)
  - `DEPLOY_USER` (example: `mydnaobv`)
  - `DEPLOY_SSH_KEY` (private key for `DEPLOY_USER`)
- Optional repository variables:
  - `DEPLOY_PORT` (default `22`)
  - `DEPLOY_APP_DIR` (default `/opt/mydnaobv/app`)
  - `DEPLOY_BRANCH` (default `main`)
  - `DEPLOY_SERVICE_NAME` (default `mydnaobv`)
  - `DEPLOY_HEALTHCHECK_URL` (default `http://127.0.0.1/`)
  - `DEPLOY_HEALTHCHECK_HOST_HEADER` (optional; set for nginx vhost host matching)
  - `DEPLOY_HEALTHCHECK_ATTEMPTS` (default `6`)
  - `DEPLOY_HEALTHCHECK_RETRY_DELAY_SECONDS` (default `5`)
  - `DEPLOY_SSH_ATTEMPTS` (default `3`)
  - `DEPLOY_SSH_RETRY_DELAY_SECONDS` (default `6`)
  - `SYSTEMCTL_USE_SUDO` (default `1`)
  - `DEPLOY_ALLOW_UNTRACKED` (default `1`)
  - `DEPLOY_ALLOW_DIRTY` (default `0`)
  - `DEPLOY_ENABLED` (`true` to enable auto deploy on push)
- Manual deploy path:
  - Actions -> "Deploy Production" -> "Run workflow"
  - optional input `run_tests=true`

## Development notes

- The iNaturalist sync logic in `app/services/inat.py` supports either user ID or username, and verifies username/ID consistency when both are provided.
- Lists can also be filtered by iNaturalist project ID/slug (`inat_project_id`).
- iNaturalist place/location filters are resolved through places lookup and applied as `place_id` to keep list sizes manageable.
- The sync logic filters results using the observation field ID (default `2330`) and also sends the field name filter (default `DNA Barcode ITS`) to the API when possible.
- Important: avoid similarly named `DNA Barcode ITS:` (with colon); that is a different field.
- All photo metadata for each synced observation is cached in `observation_photos`; export mode can use primary photo only or multiple photos.
- iNaturalist sync now caches primary photo URL/license/attribution to support compliant offline exports.
- Admin page includes county seeding by project:
  - pick a US state
  - provide project slug/ID
  - generate one list per county with `place_query` + `inat_project_id`
  - use "Process state" to queue sync+build for existing counties in that state/project
- Published downloads are written to `EXPORT_PUBLISH_DIR` as:
  - `list_<list_id>/latest/<file>`
  - `list_<list_id>/job_<job_id>/<file>`
- Pages are server-rendered (Jinja2) for simplicity and durability.
- The app uses PostgreSQL via SQLAlchemy 2.0.
- The homepage includes pagination for county download catalog.

## Environment variables

See `.env.example` for the full list.
