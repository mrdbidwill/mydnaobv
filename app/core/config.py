from pathlib import Path
import json
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", env_file_encoding="utf-8")

    app_name: str = Field(default="myDNAobv", alias="APP_NAME")
    env: str = Field(default="development", alias="ENV")
    secret_key: str = Field(default="change-me", alias="SECRET_KEY")

    database_url: str = Field(..., alias="DATABASE_URL")

    inat_base_url: str = Field(default="https://api.inaturalist.org/v1", alias="INAT_BASE_URL")
    inat_dna_field_id: Optional[str] = Field(default="2330", alias="INAT_DNA_FIELD_ID")
    inat_dna_field_name: str = Field(default="DNA Barcode ITS", alias="INAT_DNA_FIELD_NAME")
    inat_taxon_id: str = Field(default="47170", alias="INAT_TAXON_ID")
    inat_default_project_id: Optional[str] = Field(default=None, alias="INAT_DEFAULT_PROJECT_ID")
    max_observations: int = Field(default=500, alias="MAX_OBSERVATIONS")
    admin_username: str = Field(default="admin", alias="ADMIN_USERNAME")
    admin_password: str = Field(default="change-me", alias="ADMIN_PASSWORD")
    export_username: Optional[str] = Field(default=None, alias="EXPORT_USERNAME")
    export_password: Optional[str] = Field(default=None, alias="EXPORT_PASSWORD")
    export_operators_json: Optional[str] = Field(default=None, alias="EXPORT_OPERATORS_JSON")

    cache_ttl_hours: int = Field(default=24, alias="CACHE_TTL_HOURS")

    enable_pdf_exports: bool = Field(default=False, alias="ENABLE_PDF_EXPORTS")
    export_storage_dir: str = Field(default="/tmp/mydnaobv_exports", alias="EXPORT_STORAGE_DIR")
    export_retention_hours: int = Field(default=48, alias="EXPORT_RETENTION_HOURS")
    export_run_timeout_seconds: int = Field(default=35, alias="EXPORT_RUN_TIMEOUT_SECONDS")
    export_part_size: int = Field(default=100, alias="EXPORT_PART_SIZE")
    export_download_chunk_size: int = Field(default=8, alias="EXPORT_DOWNLOAD_CHUNK_SIZE")
    export_download_byte_budget_mb: int = Field(default=64, alias="EXPORT_DOWNLOAD_BYTE_BUDGET_MB")
    export_include_all_photos: bool = Field(default=False, alias="EXPORT_INCLUDE_ALL_PHOTOS")
    export_max_photos_per_observation: int = Field(default=1, alias="EXPORT_MAX_PHOTOS_PER_OBSERVATION")
    export_request_interval_seconds: float = Field(default=2.0, alias="EXPORT_REQUEST_INTERVAL_SECONDS")
    export_max_api_requests_per_day: int = Field(default=6000, alias="EXPORT_MAX_API_REQUESTS_PER_DAY")
    export_max_media_mb_per_hour: int = Field(default=3072, alias="EXPORT_MAX_MEDIA_MB_PER_HOUR")
    export_max_media_mb_per_day: int = Field(default=15360, alias="EXPORT_MAX_MEDIA_MB_PER_DAY")
    export_xs_max_items: int = Field(default=50, alias="EXPORT_XS_MAX_ITEMS")
    export_s_max_items: int = Field(default=200, alias="EXPORT_S_MAX_ITEMS")
    export_m_max_items: int = Field(default=500, alias="EXPORT_M_MAX_ITEMS")
    export_xs_cadence_minutes: int = Field(default=5, alias="EXPORT_XS_CADENCE_MINUTES")
    export_s_cadence_minutes: int = Field(default=10, alias="EXPORT_S_CADENCE_MINUTES")
    export_m_cadence_minutes: int = Field(default=20, alias="EXPORT_M_CADENCE_MINUTES")
    export_l_cadence_minutes: int = Field(default=60, alias="EXPORT_L_CADENCE_MINUTES")
    export_l_window_start_hour: int = Field(default=0, alias="EXPORT_L_WINDOW_START_HOUR")
    export_l_window_end_hour: int = Field(default=6, alias="EXPORT_L_WINDOW_END_HOUR")
    export_zip_only_part_threshold: int = Field(default=4, alias="EXPORT_ZIP_ONLY_PART_THRESHOLD")
    export_allowed_licenses: str = Field(
        default="cc0,cc-by,cc-by-sa,cc-by-nc,cc-by-nc-sa",
        alias="EXPORT_ALLOWED_LICENSES",
    )
    export_allow_unlicensed: bool = Field(default=False, alias="EXPORT_ALLOW_UNLICENSED")
    export_publish_enabled: bool = Field(default=False, alias="EXPORT_PUBLISH_ENABLED")
    export_publish_dir: Optional[str] = Field(default=None, alias="EXPORT_PUBLISH_DIR")
    export_publish_base_url: Optional[str] = Field(default=None, alias="EXPORT_PUBLISH_BASE_URL")
    export_public_downloads_enabled: bool = Field(default=False, alias="EXPORT_PUBLIC_DOWNLOADS_ENABLED")
    public_refresh_interval_days: int = Field(default=7, alias="PUBLIC_REFRESH_INTERVAL_DAYS")
    public_state_codes: str = Field(default="AL", alias="PUBLIC_STATE_CODES")

    def export_operator_credentials(self) -> list[tuple[str, str]]:
        """
        Resolve export credentials in priority order:
        1) EXPORT_OPERATORS_JSON (array of {username,password})
        2) EXPORT_USERNAME + EXPORT_PASSWORD
        3) ADMIN_USERNAME + ADMIN_PASSWORD fallback
        """
        credentials: list[tuple[str, str]] = []

        if self.export_operators_json:
            parsed = json.loads(self.export_operators_json)
            if not isinstance(parsed, list):
                raise ValueError("EXPORT_OPERATORS_JSON must be a JSON array.")
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                username = str(item.get("username") or "").strip()
                password = str(item.get("password") or "").strip()
                if username and password:
                    credentials.append((username, password))
            if credentials:
                return credentials

        if self.export_username and self.export_password:
            credentials.append((self.export_username, self.export_password))
            return credentials

        credentials.append((self.admin_username, self.admin_password))
        return credentials


settings = Settings()
