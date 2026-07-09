from pydantic import field_validator, model_validator
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

    # Comma-separated list of hostnames this API is allowed to answer for
    # (used by TrustedHostMiddleware). "*" disables the check; that's the
    # default so local/dev/CI keep working, but production deployments
    # should set this to the real backend hostname(s).
    allowed_hosts: str = "*"

    # Cron Authentication - for external cron services like cron-job.org
    cron_secret: str = ""  # Required (non-empty) in production!

    # Local playback sync loop
    enable_local_sync: bool = False
    local_sync_interval_seconds: int = 30
    sync_max_wait_seconds: int = 25

    # Timeouts
    request_timeout_seconds: float = 30.0
    db_connect_timeout_seconds: int = 5
    db_statement_timeout_ms: int = 10_000

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        lowered = value.lower()
        if "sslmode=require" not in lowered:
            raise ValueError("DATABASE_URL must include sslmode=require")
        return value

    @model_validator(mode="after")
    def validate_cron_secret_in_production(self) -> "Settings":
        if self.environment == "production" and not self.cron_secret.strip():
            raise ValueError(
                "CRON_SECRET must be set to a non-empty value when ENVIRONMENT=production"
            )
        return self

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)

    def allowed_hosts_list(self) -> list[str]:
        """Parse the comma-separated ALLOWED_HOSTS setting into a list."""
        hosts = [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]
        return hosts or ["*"]


settings = Settings()
