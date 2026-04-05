from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Spotify API
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    spotify_refresh_token: str = ""

    # Database
    database_url: str

    # CORS
    frontend_url: str = "http://localhost:3000"

    # App
    environment: str = "development"

    # Cron Authentication - for external cron services like cron-job.org
    cron_secret: str = ""  # Set this in production!

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        lowered = value.lower()
        if "sslmode=require" not in lowered:
            raise ValueError("DATABASE_URL must include sslmode=require")
        return value

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)

settings = Settings()
