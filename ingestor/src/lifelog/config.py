import os
from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    timetagger_api_url: str
    timetagger_token: str = field(repr=False)
    sync_interval_seconds: int
    dashboard_url: str
    db_dir: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env
        return cls(
            database_url=env["DATABASE_URL"],
            timetagger_api_url=env["TIMETAGGER_API_URL"].rstrip("/"),
            timetagger_token=env.get("TIMETAGGER_TOKEN", ""),
            sync_interval_seconds=int(env.get("SYNC_INTERVAL_SECONDS", "120")),
            dashboard_url=env["DASHBOARD_URL"],
            db_dir=env.get("DB_DIR", "/app/db"),
        )
