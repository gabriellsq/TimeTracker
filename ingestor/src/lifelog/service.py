from lifelog import db
from lifelog.config import Settings
from lifelog.pipeline import SyncResult, run_sync
from lifelog.sources.timetagger import TimeTaggerClient, TimeTaggerSource


def sync_once(settings: Settings, *, trigger: str, wait_for_lock: bool) -> SyncResult:
    """One sync with its own HTTP client and its own DB connection (never shared between runs)."""
    with (
        TimeTaggerClient(settings.timetagger_api_url, settings.timetagger_token) as client,
        db.connect(settings.database_url) as conn,
    ):
        return run_sync(conn, TimeTaggerSource(client), trigger=trigger, wait_for_lock=wait_for_lock)
