import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from lifelog import db, goals
from lifelog.config import Settings
from lifelog.goals_page import GoalsView, render
from lifelog.pipeline import close_stale_runs
from lifelog.service import sync_once

log = logging.getLogger("lifelog")

MAX_FORM_BYTES = 16_384

# Security headers for the goals page (no framing, no sniffing, no caching; inline CSS/JS only).
# No `form-action`: Chrome applies it to the redirect to the dashboard host and would block it.
PAGE_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
    "base-uri 'none'; frame-ancestors 'none'",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Cache-Control": "no-store",
}


async def _timer_loop(settings: Settings) -> None:
    while True:
        try:
            result = await asyncio.to_thread(sync_once, settings, trigger="timer", wait_for_lock=False)
            log.info("timer sync: %s, %d records", result.status, result.n_records)
        except Exception:
            log.exception("timer sync crashed")
        await asyncio.sleep(settings.sync_interval_seconds)


def _same_origin(request: Request) -> bool:
    """CSRF protection: a form post must come from a page on this same host."""
    source = request.headers.get("origin") or request.headers.get("referer")
    host = request.headers.get("host")
    return bool(source and host) and urlsplit(source).netloc.lower() == host.lower()


def _decimals(raw: dict[str, str]) -> dict[str, Decimal]:
    """The raw goal values that parse as numbers, so an error page can show what the user entered."""
    out = {}
    for subject, text in raw.items():
        try:
            value = Decimal(text.strip())
        except InvalidOperation:
            continue
        if value.is_finite() and 0 <= value <= goals.MAX_HOURS:  # keeps NaN and huge exponents out of the page
            out[subject] = value
    return out


def create_app(settings: Settings | None = None, *, start_timer: bool = True) -> FastAPI:
    """App factory. Run with: uvicorn --factory lifelog.app:create_app"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
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

    def _goals_view(conn, error: str | None = None, submitted: dict[str, Decimal] | None = None) -> GoalsView:
        week = goals.current_week_start(conn)
        last_week = week - timedelta(days=7)
        saved = goals.load_goals(conn, week)
        previous = goals.load_goals(conn, last_week)
        subjects = goals.list_subjects(conn)
        submitted = submitted or {}
        values = {
            s.subject: submitted.get(s.subject, saved.get(s.subject, previous.get(s.subject, Decimal(0))))
            for s in subjects
        }
        saved_state = "all" if subjects and all(s.subject in saved for s in subjects) else ("some" if saved else "none")
        return GoalsView(
            week_start=week,
            subjects=subjects,
            values=values,
            saved=saved_state,
            suggested=bool(previous),
            last_week_planned=sum(previous.values(), Decimal(0)) if previous else None,
            last_week_done=goals.study_hours(conn, last_week),
            dashboard_url=settings.dashboard_url,
            error=error,
        )

    def _save_goals(form: dict[str, list[str]]) -> Response:
        raw = {key.removeprefix("goal_"): values[0] for key, values in form.items() if key.startswith("goal_")}
        submitted = _decimals(raw)
        with db.connect(settings.database_url) as conn:
            try:
                if any(len(v) != 1 for v in form.values()):
                    raise goals.GoalError("Each field must be sent once")
                try:
                    week = date.fromisoformat(form.get("week_start", [""])[0])
                except ValueError:
                    raise goals.GoalError("Invalid week") from None
                goals.save_goals(conn, week, goals.parse_targets(raw, goals.list_subjects(conn)))
            except goals.GoalError as exc:
                page = render(_goals_view(conn, error=str(exc), submitted=submitted))
                return HTMLResponse(page, status_code=400, headers=PAGE_HEADERS)
        return RedirectResponse(settings.dashboard_url, status_code=303)

    @app.get("/goals", response_class=HTMLResponse)
    def goals_form() -> HTMLResponse:
        with db.connect(settings.database_url) as conn:
            return HTMLResponse(render(_goals_view(conn)), headers=PAGE_HEADERS)

    @app.post("/goals")
    async def goals_submit(request: Request) -> Response:
        if not _same_origin(request):
            return PlainTextResponse("Forbidden: the form must be sent from this site.", status_code=403)
        try:
            declared = int(request.headers.get("content-length", "0"))
        except ValueError:
            return PlainTextResponse("Invalid request.", status_code=400)
        if declared > MAX_FORM_BYTES:
            return PlainTextResponse("Form too large.", status_code=413)
        body = await request.body()
        if len(body) > MAX_FORM_BYTES:
            return PlainTextResponse("Form too large.", status_code=413)
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            return PlainTextResponse("Invalid form encoding.", status_code=400)
        form = parse_qs(text, keep_blank_values=True)
        return await asyncio.to_thread(_save_goals, form)

    return app
