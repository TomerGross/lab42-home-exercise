"""Tests for service_monitor/config.py: defaults, environment overrides and rejected values."""

import pytest
from pydantic import ValidationError

from service_monitor.config import Settings


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    """Remove the settings' environment variables, so values set in the shell cannot leak into a test."""
    for field in Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)


def test_defaults():
    settings = Settings()

    assert settings.database_url == "sqlite+aiosqlite:///./services.db"
    assert settings.check_timeout_seconds == 3.0
    assert settings.monitor_interval_seconds == 60.0


def test_environment_variable_overrides_default(monkeypatch):
    monkeypatch.setenv("MONITOR_INTERVAL_SECONDS", "10")

    assert Settings().monitor_interval_seconds == 10.0


@pytest.mark.parametrize("value", ["abc", "0", "-1"])
def test_invalid_value_is_rejected(monkeypatch, value):
    monkeypatch.setenv("CHECK_TIMEOUT_SECONDS", value)

    with pytest.raises(ValidationError):
        Settings()
