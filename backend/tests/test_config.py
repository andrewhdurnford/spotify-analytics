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

    def test_environment_is_normalized_before_production_validation(self):
        with pytest.raises(ValidationError):
            Settings(database_url=VALID_DB_URL, environment=" Production ", cron_secret="")

    def test_unknown_environment_fails_closed(self):
        with pytest.raises(ValidationError):
            Settings(database_url=VALID_DB_URL, environment="prod", cron_secret="secret")

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

    def test_blank_value_falls_back_to_wildcard_in_development(self, caplog):
        with caplog.at_level("WARNING"):
            settings = Settings(database_url=VALID_DB_URL, allowed_hosts="  ")
        assert settings.allowed_hosts_list() == ["*"]
        assert "host checking is disabled" in caplog.text

    def test_production_with_blank_allowed_hosts_fails_to_construct(self):
        """Blank would silently fail open to "*"; in production that's a typo."""
        with pytest.raises(ValidationError):
            Settings(
                database_url=VALID_DB_URL,
                environment="production",
                cron_secret="real-secret",
                allowed_hosts="  ",
            )

    def test_production_with_explicit_wildcard_is_allowed(self):
        settings = Settings(
            database_url=VALID_DB_URL,
            environment="production",
            cron_secret="real-secret",
            allowed_hosts="*",
        )
        assert settings.allowed_hosts_list() == ["*"]


class TestDatabaseSsl:
    def test_url_without_sslmode_fails_by_default(self):
        with pytest.raises(ValidationError):
            Settings(database_url="postgresql://test:test@db/test")

    def test_url_without_sslmode_allowed_when_ssl_not_required(self):
        settings = Settings(
            database_url="postgresql://test:test@db/test", database_require_ssl=False
        )
        assert settings.database_url == "postgresql://test:test@db/test"
