"""Settings, each overridable by the environment variable of the same name (e.g. MONITOR_INTERVAL_SECONDS=10)."""

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:///./services.db"
    check_timeout_seconds: float = Field(default=3.0, gt=0)
    monitor_interval_seconds: float = Field(default=60.0, gt=0)
