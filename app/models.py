from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional
from sqlalchemy import String, Integer, DateTime, ForeignKey, UniqueConstraint, Text, BigInteger
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
    user_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    observed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    inat_url: Mapped[str] = mapped_column(String(512))
    dna_field_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    photo_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    photo_license_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    photo_attribution: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    list_id: Mapped[int] = mapped_column(ForeignKey("observation_lists.id"), index=True)

    list: Mapped[ObservationList] = relationship(back_populates="observations")
    photos: Mapped[list["ObservationPhoto"]] = relationship(
        "ObservationPhoto",
        back_populates="observation",
        uselist=True,
    )
    export_items: Mapped[list["ExportItem"]] = relationship("ExportItem", back_populates="observation")


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
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    next_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    list: Mapped[ObservationList] = relationship("ObservationList", back_populates="export_jobs")
    items: Mapped[list["ExportItem"]] = relationship("ExportItem", back_populates="job")
    artifacts: Mapped[list["ExportArtifact"]] = relationship("ExportArtifact", back_populates="job")


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
