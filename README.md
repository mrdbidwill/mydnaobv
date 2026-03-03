# myDNAobv

A lightweight, maintainable FastAPI app that displays iNaturalist observations filtered by the “DNA Barcode ITS” observation field. The app stores named, shareable lists keyed by an iNaturalist **numeric user ID**.

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

## Development notes

- The iNaturalist sync logic in `app/services/inat.py` filters results using the observation field ID (default `18776`) and also sends the field name filter (default `DNA Barcode ITS`) to the API when possible.
- Pages are server-rendered (Jinja2) for simplicity and durability.
- The app uses PostgreSQL via SQLAlchemy 2.0.
- The homepage includes pagination for saved lists.

## Environment variables

See `.env.example` for the full list.
