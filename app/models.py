from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Optional
from sqlalchemy import String, Integer, DateTime, Date, Float, ForeignKey, UniqueConstraint, Text, BigInteger, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db import Base


def utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class ObservationList(Base):
    __tablename__ = "observation_lists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    inat_user_id: Mapped[Optional[int]] = mapped_column(Integer, index=True, nullable=True)
    inat_username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    inat_project_id: Mapped[Optional[str]] = mapped_column(String(255), index=True, nullable=True)
    product_type: Mapped[str] = mapped_column(String(32), default="custom", index=True)
    state_code: Mapped[Optional[str]] = mapped_column(String(2), index=True, nullable=True)
    county_name: Mapped[Optional[str]] = mapped_column(String(255), index=True, nullable=True)
    is_public_download: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    inat_place_id: Mapped[Optional[int]] = mapped_column(Integer, index=True, nullable=True)
    place_query: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    inat_dna_field_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    taxon_filter: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    observations: Mapped[list["Observation"]] = relationship(back_populates="list")
    export_jobs: Mapped[list["ExportJob"]] = relationship("ExportJob", back_populates="list")


class Observation(Base):
    __tablename__ = "observations"
    __table_args__ = (
        UniqueConstraint("list_id", "inat_observation_id", name="uq_observation_list_inat_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    inat_observation_id: Mapped[int] = mapped_column(Integer, index=True)
    taxon_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    species_guess: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    scientific_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    common_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    observation_taxon_id: Mapped[Optional[int]] = mapped_column(Integer, index=True, nullable=True)
    observation_taxon_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    observation_taxon_rank: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    community_taxon_id: Mapped[Optional[int]] = mapped_column(Integer, index=True, nullable=True)
    community_taxon_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    community_taxon_rank: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    observed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    inat_url: Mapped[str] = mapped_column(String(512))
    dna_field_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    photo_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    photo_license_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    photo_attribution: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    list_id: Mapped[int] = mapped_column(ForeignKey("observation_lists.id"), index=True)

    list: Mapped[ObservationList] = relationship(back_populates="observations")
    photos = relationship(
        "ObservationPhoto",
        back_populates="observation",
        uselist=True,
    )
    export_items = relationship("ExportItem", back_populates="observation", uselist=True)


class ObservationPhoto(Base):
    __tablename__ = "observation_photos"
    __table_args__ = (
        UniqueConstraint("observation_id", "photo_index", name="uq_observation_photo_index"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    observation_id: Mapped[int] = mapped_column(ForeignKey("observations.id"), index=True)
    inat_photo_id: Mapped[Optional[int]] = mapped_column(Integer, index=True, nullable=True)
    photo_index: Mapped[int] = mapped_column(Integer, default=1)
    photo_url: Mapped[str] = mapped_column(String(1024))
    photo_license_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    photo_attribution: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)

    observation: Mapped[Observation] = relationship("Observation", back_populates="photos")


class ExportJob(Base):
    __tablename__ = "export_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    list_id: Mapped[int] = mapped_column(ForeignKey("observation_lists.id"), index=True)
    requested_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    phase: Mapped[str] = mapped_column(String(32), default="plan")
    size_bucket: Mapped[Optional[str]] = mapped_column(String(8), nullable=True, index=True)

    total_items: Mapped[int] = mapped_column(Integer, default=0)
    eligible_items: Mapped[int] = mapped_column(Integer, default=0)
    downloaded_items: Mapped[int] = mapped_column(Integer, default=0)
    rendered_items: Mapped[int] = mapped_column(Integer, default=0)
    skipped_items: Mapped[int] = mapped_column(Integer, default=0)
    failed_items: Mapped[int] = mapped_column(Integer, default=0)

    api_requests: Mapped[int] = mapped_column(Integer, default=0)
    bytes_downloaded: Mapped[int] = mapped_column(BigInteger, default=0)
    part_size: Mapped[int] = mapped_column(Integer, default=100)
    force_sync: Mapped[bool] = mapped_column(Boolean, default=False)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    next_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    list: Mapped[ObservationList] = relationship("ObservationList", back_populates="export_jobs")
    items = relationship("ExportItem", back_populates="job", uselist=True)
    artifacts = relationship("ExportArtifact", back_populates="job", uselist=True)


class ExportItem(Base):
    __tablename__ = "export_items"
    __table_args__ = (
        UniqueConstraint("job_id", "sequence", name="uq_export_item_job_sequence"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("export_jobs.id"), index=True)
    observation_id: Mapped[Optional[int]] = mapped_column(ForeignKey("observations.id"), nullable=True, index=True)
    sequence: Mapped[int] = mapped_column(Integer, index=True)
    inat_observation_id: Mapped[int] = mapped_column(Integer, index=True)
    item_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    observation_taxon_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    community_taxon_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    observed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    inat_url: Mapped[str] = mapped_column(String(512))
    image_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    image_license_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    image_attribution: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    local_image_relpath: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    part_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    skip_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)

    job: Mapped[ExportJob] = relationship("ExportJob", back_populates="items")
    observation: Mapped[Optional[Observation]] = relationship("Observation", back_populates="export_items")


class ExportArtifact(Base):
    __tablename__ = "export_artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("export_jobs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    part_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    relative_path: Mapped[str] = mapped_column(String(1024))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)

    job: Mapped[ExportJob] = relationship("ExportJob", back_populates="artifacts")


class CatalogSource(Base):
    __tablename__ = "catalog_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    project_numeric_id: Mapped[Optional[int]] = mapped_column(Integer, index=True, nullable=True)
    project_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_sync_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    observation_links: Mapped[list["CatalogObservationProject"]] = relationship(
        "CatalogObservationProject",
        back_populates="source",
        uselist=True,
    )


class CatalogObservation(Base):
    __tablename__ = "catalog_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    inat_observation_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    uri: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    taxon_id: Mapped[Optional[int]] = mapped_column(Integer, index=True, nullable=True)
    taxon_name: Mapped[Optional[str]] = mapped_column(String(255), index=True, nullable=True)
    taxon_rank: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    community_taxon_id: Mapped[Optional[int]] = mapped_column(Integer, index=True, nullable=True)
    community_taxon_name: Mapped[Optional[str]] = mapped_column(String(255), index=True, nullable=True)
    community_taxon_rank: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    species_guess: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    user_login: Mapped[Optional[str]] = mapped_column(String(255), index=True, nullable=True)
    quality_grade: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
    observed_on: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    observed_on_date: Mapped[Optional[date]] = mapped_column(Date, index=True, nullable=True)
    observed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    inat_created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True, nullable=True)
    inat_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True, nullable=True)
    place_guess: Mapped[Optional[str]] = mapped_column(String(255), index=True, nullable=True)
    location: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    geoprivacy: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    genus_key: Mapped[Optional[str]] = mapped_column(String(128), index=True, nullable=True)
    primary_photo_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    primary_photo_license_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    primary_photo_attribution: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    photo_count: Mapped[int] = mapped_column(Integer, default=0)
    raw_payload: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)

    source_links: Mapped[list["CatalogObservationProject"]] = relationship(
        "CatalogObservationProject",
        back_populates="observation",
        uselist=True,
    )


class CatalogObservationProject(Base):
    __tablename__ = "catalog_observation_projects"
    __table_args__ = (
        UniqueConstraint("source_id", "observation_id", name="uq_catalog_source_observation"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("catalog_sources.id"), index=True)
    observation_id: Mapped[int] = mapped_column(ForeignKey("catalog_observations.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)

    source: Mapped[CatalogSource] = relationship("CatalogSource", back_populates="observation_links")
    observation: Mapped[CatalogObservation] = relationship("CatalogObservation", back_populates="source_links")
