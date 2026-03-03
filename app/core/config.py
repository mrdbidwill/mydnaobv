from pathlib import Path
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
    inat_dna_field_id: Optional[str] = Field(default=None, alias="INAT_DNA_FIELD_ID")
    inat_dna_field_name: str = Field(default="DNA Barcode ITS", alias="INAT_DNA_FIELD_NAME")
    inat_taxon_id: str = Field(default="47170", alias="INAT_TAXON_ID")
    max_observations: int = Field(default=500, alias="MAX_OBSERVATIONS")
    admin_username: str = Field(default="admin", alias="ADMIN_USERNAME")
    admin_password: str = Field(default="change-me", alias="ADMIN_PASSWORD")

    cache_ttl_hours: int = Field(default=24, alias="CACHE_TTL_HOURS")


settings = Settings()
