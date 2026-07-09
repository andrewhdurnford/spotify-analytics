"""Unit tests for Settings validation."""

import pytest
from pydantic import ValidationError

from app.config import Settings

VALID_DB_URL = "postgresql://test:test@localhost/test?sslmode=require"


class TestCronSecretProductionRequirement:
    def test_production_with_empty_cron_secret_fails_to_construct(self):
        with pytest.raises(ValidationError):
            Settings(database_url=VALID_DB_URL, environment="production", cron_secret="")

    def test_production_with_whitespace_only_cron_secret_fails_to_construct(self):
        with pytest.raises(ValidationError):
            Settings(database_url=VALID_DB_URL, environment="production", cron_secret="   ")

    def test_production_with_non_empty_cron_secret_succeeds(self):
        settings = Settings(
            database_url=VALID_DB_URL, environment="production", cron_secret="real-secret"
        )
        assert settings.cron_secret == "real-secret"

    def test_development_with_empty_cron_secret_succeeds(self):
        settings = Settings(database_url=VALID_DB_URL, environment="development", cron_secret="")
        assert settings.cron_secret == ""


class TestAllowedHostsList:
    def test_default_is_wildcard(self):
        settings = Settings(database_url=VALID_DB_URL)
        assert settings.allowed_hosts_list() == ["*"]

    def test_parses_comma_separated_hosts(self):
        settings = Settings(
            database_url=VALID_DB_URL, allowed_hosts="api.example.com, api2.example.com"
        )
        assert settings.allowed_hosts_list() == ["api.example.com", "api2.example.com"]

    def test_blank_value_falls_back_to_wildcard(self):
        settings = Settings(database_url=VALID_DB_URL, allowed_hosts="  ")
        assert settings.allowed_hosts_list() == ["*"]
