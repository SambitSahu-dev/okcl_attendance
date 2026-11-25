from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./attendance.db"
    secret_key: str | None = None
    face_embedding_key: str | None = None
    face_match_threshold: float = 0.363
    kiosk_id: str = "sample-kiosk"
    kiosk_name: str = "Sample Kiosk"
    kiosk_office: str = "Sample Office"
    kiosk_latitude: float = 0.0
    kiosk_longitude: float = 0.0
    admin_username: str = "admin"
    admin_password: str | None = None
    super_admin_username: str | None = None
    super_admin_password: str | None = None
    admin_contact_email: str | None = None
    log_level: str = "INFO"
    scan_start_limit_per_minute: int = Field(default=10, ge=1)
    scan_frame_limit_per_minute: int = Field(default=400, ge=1)
    image_retention_days: int = Field(default=365, ge=1)
    backup_retention_days: int = Field(default=30, ge=1)

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()
