from pydantic_settings import BaseSettings, SettingsConfigDict
import os

class Settings(BaseSettings):
    # Spotify API
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    spotify_refresh_token: str = ""
    
    # Database - defaults to local, override with env var for prod
    database_url: str = "postgresql://spotify:spotify@db:5432/spotify_analytics"
    
    # CORS
    frontend_url: str = "http://localhost:3000"
    
    # App
    environment: str = "development"
    
    # Cron Authentication - for external cron services like cron-job.org
    cron_secret: str = ""  # Set this in production!
    
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)

settings = Settings()