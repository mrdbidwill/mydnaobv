from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.core.config import settings


@dataclass(frozen=True)
class ExportConfig:
    enabled: bool
    storage_dir: str
    retention_hours: int
    run_timeout_seconds: int
    part_size: int
    download_chunk_size: int
    download_byte_budget_mb: int
    image_cache_enabled: bool
    image_cache_ttl_days: int
    image_cache_retention_days: int
    image_cache_prune_interval_hours: int
    image_cache_max_prune_files: int
    include_all_photos: bool
    max_photos_per_observation: int
    request_interval_seconds: float
    max_api_requests_per_day: int
    max_media_mb_per_hour: int
    max_media_mb_per_day: int
    xs_max_items: int
    s_max_items: int
    m_max_items: int
    xs_cadence_minutes: int
    s_cadence_minutes: int
    m_cadence_minutes: int
    l_cadence_minutes: int
    l_window_start_hour: int
    l_window_end_hour: int
    zip_only_part_threshold: int
    allowed_licenses_csv: str
    allow_unlicensed: bool
    publish_enabled: bool
    publish_backend: str
    publish_dir: str | None
    publish_base_url: str | None
    publish_bucket: str | None
    publish_prefix: str
    publish_s3_endpoint: str | None
    publish_s3_region: str
    publish_s3_access_key_id: str | None
    publish_s3_secret_access_key: str | None
    public_downloads_enabled: bool

    def classify_bucket(self, item_count: int) -> str:
        if item_count <= self.xs_max_items:
            return "XS"
        if item_count <= self.s_max_items:
            return "S"
        if item_count <= self.m_max_items:
            return "M"
        return "L"

    def cadence_for_bucket(self, bucket: str | None) -> timedelta:
        if bucket == "XS":
            return timedelta(minutes=self.xs_cadence_minutes)
        if bucket == "S":
            return timedelta(minutes=self.s_cadence_minutes)
        if bucket == "M":
            return timedelta(minutes=self.m_cadence_minutes)
        return timedelta(minutes=self.l_cadence_minutes)

    def is_large_window_open(self, now: datetime) -> bool:
        now_utc = _as_utc(now)
        hour = now_utc.hour
        if self.l_window_start_hour <= self.l_window_end_hour:
            return self.l_window_start_hour <= hour < self.l_window_end_hour
        return hour >= self.l_window_start_hour or hour < self.l_window_end_hour

    def next_large_window_start(self, now: datetime) -> datetime:
        now_utc = _as_utc(now)
        start_today = now_utc.replace(
            hour=self.l_window_start_hour,
            minute=0,
            second=0,
            microsecond=0,
            tzinfo=UTC,
        )
        if now_utc < start_today:
            return start_today.replace(tzinfo=None)
        return (start_today + timedelta(days=1)).replace(tzinfo=None)

    @property
    def allowed_licenses(self) -> set[str]:
        out: set[str] = set()
        for token in self.allowed_licenses_csv.split(","):
            value = token.strip().lower()
            if value:
                out.add(value)
        return out


export_config = ExportConfig(
    enabled=settings.enable_pdf_exports,
    storage_dir=settings.export_storage_dir,
    retention_hours=settings.export_retention_hours,
    run_timeout_seconds=settings.export_run_timeout_seconds,
    part_size=settings.export_part_size,
    download_chunk_size=settings.export_download_chunk_size,
    download_byte_budget_mb=settings.export_download_byte_budget_mb,
    image_cache_enabled=settings.export_image_cache_enabled,
    image_cache_ttl_days=settings.export_image_cache_ttl_days,
    image_cache_retention_days=settings.export_image_cache_retention_days,
    image_cache_prune_interval_hours=settings.export_image_cache_prune_interval_hours,
    image_cache_max_prune_files=settings.export_image_cache_max_prune_files,
    include_all_photos=settings.export_include_all_photos,
    max_photos_per_observation=settings.export_max_photos_per_observation,
    request_interval_seconds=settings.export_request_interval_seconds,
    max_api_requests_per_day=settings.export_max_api_requests_per_day,
    max_media_mb_per_hour=settings.export_max_media_mb_per_hour,
    max_media_mb_per_day=settings.export_max_media_mb_per_day,
    xs_max_items=settings.export_xs_max_items,
    s_max_items=settings.export_s_max_items,
    m_max_items=settings.export_m_max_items,
    xs_cadence_minutes=settings.export_xs_cadence_minutes,
    s_cadence_minutes=settings.export_s_cadence_minutes,
    m_cadence_minutes=settings.export_m_cadence_minutes,
    l_cadence_minutes=settings.export_l_cadence_minutes,
    l_window_start_hour=settings.export_l_window_start_hour,
    l_window_end_hour=settings.export_l_window_end_hour,
    zip_only_part_threshold=settings.export_zip_only_part_threshold,
    allowed_licenses_csv=settings.export_allowed_licenses,
    allow_unlicensed=settings.export_allow_unlicensed,
    publish_enabled=settings.export_publish_enabled,
    publish_backend=settings.export_publish_backend,
    publish_dir=settings.export_publish_dir,
    publish_base_url=settings.export_publish_base_url,
    publish_bucket=settings.export_publish_bucket,
    publish_prefix=settings.export_publish_prefix,
    publish_s3_endpoint=settings.export_publish_s3_endpoint,
    publish_s3_region=settings.export_publish_s3_region,
    publish_s3_access_key_id=settings.export_publish_s3_access_key_id,
    publish_s3_secret_access_key=settings.export_publish_s3_secret_access_key,
    public_downloads_enabled=settings.export_public_downloads_enabled,
)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)
