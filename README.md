# myDNAobv

A lightweight, maintainable FastAPI app that displays iNaturalist observations filtered by the “DNA Barcode ITS” observation field. Lists can be keyed by iNaturalist numeric user ID, username, and optional county/address filter.

## Quick start

1. Create a virtual environment and install deps:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

2. Configure environment:

```bash
cp .env.example .env
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

## Queued PDF exports (KVM 1 profile)

This project now supports a modular, queue-based PDF export pipeline for offline use.

- Exports never run inline with the web request.
- A worker processes one job at a time in short slices (`--once`).
- Jobs are prioritized smallest to largest (`XS`, `S`, `M`, `L`).
- Large jobs are limited to an overnight window.
- If merge pressure is high, output degrades gracefully to split PDFs + ZIP.
- Queue requests use a stale detector: no new job is created when list data has not changed since the latest completed export.
- Public list creation and browsing remain unchanged.
- Heavy export controls live on a separate authenticated page: `/exports`.
- Optional publish mode copies finished files to external/static storage and exposes a public member page: `/downloads`.
- Optional mode: include multiple photos per observation with conservative KVM1 caps.
- Create/edit flow now includes a fast pre-check estimate, and list/export pages show synced-data ETA ranges before queueing.
- Export access supports:
  - `EXPORT_OPERATORS_JSON` (preferred, multiple operator accounts), or
  - `EXPORT_USERNAME` / `EXPORT_PASSWORD` (single account), or
  - admin credentials fallback if neither is configured.

Example multi-operator config:

```env
EXPORT_OPERATORS_JSON=[{"username":"ams_alice","password":"strong-pass-1"},{"username":"ams_bob","password":"strong-pass-2"}]
```

Example multi-photo export mode (KVM1-safe starting point):

```env
EXPORT_INCLUDE_ALL_PHOTOS=true
EXPORT_MAX_PHOTOS_PER_OBSERVATION=3
EXPORT_DOWNLOAD_CHUNK_SIZE=4
EXPORT_PART_SIZE=60
```

Example published member downloads mode:

```env
EXPORT_PUBLISH_ENABLED=true
EXPORT_PUBLISH_DIR=/var/www/mydnaobv-downloads
EXPORT_PUBLISH_BASE_URL=https://downloads.example.org/mydnaobv
EXPORT_PUBLIC_DOWNLOADS_ENABLED=true
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

Suggested cron entries (KVM 1):

```cron
*/5 * * * * flock -n /var/lock/mydnaobv_export.lock timeout 45s nice -n 15 ionice -c2 -n7 /usr/bin/python3 -m app.exports.worker --once
17 3 * * * /usr/bin/python3 -m app.exports.worker --cleanup
```

## Development notes

- The iNaturalist sync logic in `app/services/inat.py` supports either user ID or username, and verifies username/ID consistency when both are provided.
- County/address filters are resolved through iNaturalist places lookup and applied as `place_id` to keep list sizes manageable.
- The sync logic filters results using the observation field ID (default `18776`) and also sends the field name filter (default `DNA Barcode ITS`) to the API when possible.
- All photo metadata for each synced observation is cached in `observation_photos`; export mode can use primary photo only or multiple photos.
- iNaturalist sync now caches primary photo URL/license/attribution to support compliant offline exports.
- Published downloads are written to `EXPORT_PUBLISH_DIR` as:
  - `list_<list_id>/latest/<file>`
  - `list_<list_id>/job_<job_id>/<file>`
- Pages are server-rendered (Jinja2) for simplicity and durability.
- The app uses PostgreSQL via SQLAlchemy 2.0.
- The homepage includes pagination for saved lists.

## Environment variables

See `.env.example` for the full list.
