import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse, RedirectResponse

from lifelog import db
from lifelog.config import Settings
from lifelog.pipeline import close_stale_runs
from lifelog.service import sync_once

log = logging.getLogger("lifelog")


async def _timer_loop(settings: Settings) -> None:
    while True:
        try:
            result = await asyncio.to_thread(sync_once, settings, trigger="timer", wait_for_lock=False)
            log.info("timer sync: %s, %d records", result.status, result.n_records)
        except Exception:
            log.exception("timer sync crashed")
        await asyncio.sleep(settings.sync_interval_seconds)


def create_app(settings: Settings | None = None, *, start_timer: bool = True) -> FastAPI:
    """App factory. Run with: uvicorn --factory lifelog.app:create_app"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        with db.wait_for_db(settings.database_url) as conn:
            log.info("applied migrations: %s", db.setup(conn, Path(settings.db_dir)) or "none")
            if stale := close_stale_runs(conn):
                log.warning("marked %d interrupted sync run(s) as failed", stale)
        task = asyncio.create_task(_timer_loop(settings)) if start_timer else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="lifelog ingestor", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, str]:
        with db.connect(settings.database_url) as conn:
            conn.execute("SELECT 1")
        return {"status": "ok"}

    @app.get("/sync")
    def sync():
        result = sync_once(settings, trigger="button", wait_for_lock=True)
        if result.status == "failed":
            return PlainTextResponse(f"Sync failed: {result.error}", status_code=502)
        return RedirectResponse(settings.dashboard_url, status_code=303)

    return app
