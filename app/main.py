from datetime import datetime, timedelta
from fastapi import FastAPI, Request, Form, Depends, Query, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session
import secrets

from app.core.config import settings
from app.db import get_db
from app import models
from app.services.inat import fetch_observations_for_list


templates = Jinja2Templates(directory="app/templates")

app = FastAPI(title=settings.app_name)
security = HTTPBasic()


PAGE_SIZE = 10
OBS_PAGE_SIZE = 15


@app.get("/")
def index(request: Request, page: int = Query(default=1, ge=1), db: Session = Depends(get_db)):
    total = db.query(func.count(models.ObservationList.id)).scalar() or 0
    lists = (
        db.query(models.ObservationList)
        .order_by(models.ObservationList.created_at.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
        .all()
    )

    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "lists": lists,
            "page": page,
            "pages": pages,
            "dna_field_id": settings.inat_dna_field_id or "",
        },
    )


@app.post("/lists/create")
def create_list(
    request: Request,
    title: str = Form(...),
    description: str = Form(default=""),
    inat_user_id: str = Form(...),
    inat_username: str = Form(default=""),
    dna_field_id: str = Form(default=""),
    taxon_filter: str = Form(default=""),
    db: Session = Depends(get_db),
):
    title = title.strip()
    if not title:
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "app_name": settings.app_name,
                "error": "Please provide a list title.",
                "lists": [],
                "page": 1,
                "pages": 1,
                "dna_field_id": dna_field_id or settings.inat_dna_field_id or "",
            },
            status_code=400,
        )

    try:
        user_id_int = int(inat_user_id)
        if user_id_int <= 0:
            raise ValueError
    except ValueError:
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "app_name": settings.app_name,
                "error": "Please provide a valid numeric iNaturalist user ID.",
                "lists": [],
                "page": 1,
                "pages": 1,
                "dna_field_id": dna_field_id or settings.inat_dna_field_id or "",
            },
            status_code=400,
        )

    obs_list = models.ObservationList(
        title=title,
        description=description.strip() or None,
        inat_user_id=user_id_int,
        inat_username=inat_username.strip() or None,
        inat_dna_field_id=dna_field_id.strip() or settings.inat_dna_field_id,
        taxon_filter=taxon_filter.strip() or None,
    )
    db.add(obs_list)
    db.commit()
    db.refresh(obs_list)

    return RedirectResponse(url=f"/lists/{obs_list.id}", status_code=303)


@app.get("/lists/{list_id}")
def list_page(
    request: Request,
    list_id: int,
    refresh: bool = False,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    obs_list = db.query(models.ObservationList).filter_by(id=list_id).first()
    if not obs_list:
        return templates.TemplateResponse(
            "list.html",
            {
                "request": request,
                "list": None,
                "observations": [],
                "error": "List not found.",
            },
            status_code=404,
        )

    ttl = timedelta(hours=settings.cache_ttl_hours)
    needs_sync = (
        refresh
        or not obs_list.last_sync_at
        or (datetime.utcnow() - obs_list.last_sync_at) > ttl
    )

    sync_error = None
    if needs_sync and obs_list.inat_dna_field_id:
        try:
            observations = fetch_observations_for_list(obs_list)
            for obs in observations:
                existing = (
                    db.query(models.Observation)
                    .filter_by(list_id=obs_list.id, inat_observation_id=obs.inat_id)
                    .first()
                )
                if existing:
                    existing.taxon_name = obs.taxon_name
                    existing.species_guess = obs.species_guess
                    existing.scientific_name = obs.scientific_name
                    existing.common_name = obs.common_name
                    existing.user_name = obs.user_name
                    existing.observed_at = obs.observed_at
                    existing.inat_url = obs.inat_url
                    existing.dna_field_value = obs.dna_field_value
                    continue
                record = models.Observation(
                    inat_observation_id=obs.inat_id,
                    taxon_name=obs.taxon_name,
                    species_guess=obs.species_guess,
                    scientific_name=obs.scientific_name,
                    common_name=obs.common_name,
                    user_name=obs.user_name,
                    observed_at=obs.observed_at,
                    inat_url=obs.inat_url,
                    dna_field_value=obs.dna_field_value,
                    list_id=obs_list.id,
                )
                db.add(record)
            obs_list.last_sync_at = datetime.utcnow()
            db.commit()
        except Exception as exc:
            db.rollback()
            sync_error = f"Sync failed: {exc}"

    total_obs = (
        db.query(func.count(models.Observation.id))
        .filter_by(list_id=obs_list.id)
        .scalar()
        or 0
    )
    obs_pages = max(1, (total_obs + OBS_PAGE_SIZE - 1) // OBS_PAGE_SIZE)
    observations = (
        db.query(models.Observation)
        .filter_by(list_id=obs_list.id)
        .order_by(models.Observation.observed_at.desc().nullslast())
        .offset((page - 1) * OBS_PAGE_SIZE)
        .limit(OBS_PAGE_SIZE)
        .all()
    )

    return templates.TemplateResponse(
        "list.html",
        {
            "request": request,
            "list": obs_list,
            "observations": observations,
            "cache_ttl_hours": settings.cache_ttl_hours,
            "sync_error": sync_error,
            "page": page,
            "pages": obs_pages,
            "max_observations": settings.max_observations,
        },
    )


def require_admin(credentials: HTTPBasicCredentials = Depends(security)):
    username_ok = secrets.compare_digest(credentials.username, settings.admin_username)
    password_ok = secrets.compare_digest(credentials.password, settings.admin_password)
    if not (username_ok and password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )
    return True


@app.get("/admin")
def admin_page(
    request: Request,
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    lists = (
        db.query(models.ObservationList)
        .order_by(models.ObservationList.created_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        "admin.html",
        {"request": request, "lists": lists, "max_observations": settings.max_observations},
    )


@app.post("/admin/lists/{list_id}/delete")
def admin_delete_list(
    list_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    db.query(models.Observation).filter_by(list_id=list_id).delete(synchronize_session=False)
    db.query(models.ObservationList).filter_by(id=list_id).delete(synchronize_session=False)
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/lists/{list_id}/sync")
def admin_sync_list(
    list_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    obs_list = db.query(models.ObservationList).filter_by(id=list_id).first()
    if not obs_list:
        return RedirectResponse(url="/admin", status_code=303)
    obs_list.last_sync_at = None
    db.commit()
    return RedirectResponse(url=f"/lists/{list_id}?refresh=true", status_code=303)


@app.post("/lists/{list_id}/edit")
def edit_list(
    request: Request,
    list_id: int,
    title: str = Form(...),
    description: str = Form(default=""),
    inat_user_id: str = Form(...),
    inat_username: str = Form(default=""),
    dna_field_id: str = Form(default=""),
    taxon_filter: str = Form(default=""),
    db: Session = Depends(get_db),
):
    obs_list = db.query(models.ObservationList).filter_by(id=list_id).first()
    if not obs_list:
        return templates.TemplateResponse(
            "list.html",
            {
                "request": request,
                "list": None,
                "observations": [],
                "error": "List not found.",
            },
            status_code=404,
        )

    title = title.strip()
    if not title:
        return templates.TemplateResponse(
            "list.html",
            {
                "request": request,
                "list": obs_list,
                "observations": [],
                "error": "Title is required.",
            },
            status_code=400,
        )

    try:
        user_id_int = int(inat_user_id)
        if user_id_int <= 0:
            raise ValueError
    except ValueError:
        return templates.TemplateResponse(
            "list.html",
            {
                "request": request,
                "list": obs_list,
                "observations": [],
                "error": "Please provide a valid numeric iNaturalist user ID.",
            },
            status_code=400,
        )

    obs_list.title = title
    obs_list.description = description.strip() or None
    obs_list.inat_user_id = user_id_int
    obs_list.inat_username = inat_username.strip() or None
    obs_list.inat_dna_field_id = dna_field_id.strip() or settings.inat_dna_field_id
    obs_list.taxon_filter = taxon_filter.strip() or None
    db.commit()

    return RedirectResponse(url=f"/lists/{obs_list.id}", status_code=303)
