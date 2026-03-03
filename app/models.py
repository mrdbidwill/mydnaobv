from datetime import UTC, datetime
from typing import Optional
from sqlalchemy import String, Integer, DateTime, ForeignKey, UniqueConstraint, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db import Base


def utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class ObservationList(Base):
    __tablename__ = "observation_lists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    inat_user_id: Mapped[int] = mapped_column(Integer, index=True)
    inat_username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    inat_dna_field_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    taxon_filter: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    observations: Mapped[list["Observation"]] = relationship(back_populates="list")


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

    list_id: Mapped[int] = mapped_column(ForeignKey("observation_lists.id"), index=True)

    list: Mapped[ObservationList] = relationship(back_populates="observations")
