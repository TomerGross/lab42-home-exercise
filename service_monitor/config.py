"""The application's settings.

Each setting can be overridden by the environment variable of the same name in
capitals, e.g. MONITOR_INTERVAL_SECONDS=10. Invalid values stop the server at
startup.
"""

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Where data is stored, how long a check may take, and how often services are re-checked."""

    database_url: str = "sqlite+aiosqlite:///./services.db"
    check_timeout_seconds: float = Field(default=3.0, gt=0)  # one check, including the reply
    monitor_interval_seconds: float = Field(default=60.0, gt=0)  # pause between background monitoring rounds
