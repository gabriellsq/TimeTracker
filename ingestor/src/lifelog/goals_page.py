"""HTML for the weekly goals page: one self-contained page, no framework, works on a phone."""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from html import escape

from lifelog.goals import MAX_HOURS, STEP, Subject


@dataclass(frozen=True)
class GoalsView:
    week_start: date
    subjects: list[Subject]
    values: dict[str, Decimal]  # what the sliders show, per subject
    saved: str  # "all" | "some" | "none": how many subjects have a saved goal this week
    suggested: bool  # last week had goals
    last_week_planned: Decimal | None
    last_week_done: Decimal
    dashboard_url: str
    error: str | None = None


def hours(value: Decimal) -> str:
    """8.0 -> '8', 4.5 -> '4.5'."""
    return f"{value.normalize():f}"


def week_label(start: date) -> str:
    end = start + timedelta(days=6)
    return f"{start:%b} {start.day} – {end:%b} {end.day}"


_STYLE = """
:root { color-scheme: light dark; font-family: system-ui, -apple-system, sans-serif; }
body { margin: 0; padding: 24px 16px; display: flex; justify-content: center; }
main { width: 100%; max-width: 420px; }
h1 { font-size: 22px; font-weight: 600; margin: 0 0 4px; }
.muted { color: GrayText; font-size: 14px; margin: 0 0 20px; }
.row { margin-bottom: 20px; }
.row label { display: flex; justify-content: space-between; font-size: 16px; margin-bottom: 6px; }
input[type=range] { width: 100%; }
.total { display: flex; justify-content: space-between; border-top: 1px solid GrayText;
         padding-top: 12px; font-size: 16px; }
.note { font-size: 13px; color: GrayText; margin-top: 8px; }
.error { color: #d33; font-size: 14px; margin-bottom: 16px; }
button { width: 100%; margin-top: 20px; padding: 12px; font-size: 16px; border-radius: 10px; }
a { display: block; text-align: center; margin-top: 16px; font-size: 14px; }
"""

_SCRIPT = """
const sliders = document.querySelectorAll('input[type=range]');
function update() {
  let total = 0;
  sliders.forEach(s => {
    document.getElementById('out_' + s.dataset.subject).textContent = s.value + ' h';
    total += parseFloat(s.value);
  });
  document.getElementById('total').textContent = total + ' h';
}
sliders.forEach(s => s.addEventListener('input', update));
update();
window.addEventListener('pageshow', update);
"""


def render(view: GoalsView) -> str:
    rows = []
    for s in view.subjects:
        value = hours(view.values.get(s.subject, Decimal(0)))
        key = escape(s.subject)
        rows.append(
            f'<div class="row"><label for="goal_{key}"><span>{escape(s.label)}</span>'
            f'<span id="out_{key}">{value} h</span></label>'
            f'<input type="range" id="goal_{key}" name="goal_{key}" data-subject="{key}" '
            f'min="0" max="{hours(MAX_HOURS)}" step="{hours(STEP)}" value="{value}"></div>'
        )
    total = hours(sum((view.values.get(s.subject, Decimal(0)) for s in view.subjects), Decimal(0)))
    if view.saved == "all":
        status = "Saved"
    elif view.saved == "some":
        status = "Some subjects are not saved yet"
    elif view.suggested:
        status = "Suggested from last week, not saved yet"
    else:
        status = "No goals yet"
    planned = "no goal" if view.last_week_planned is None else f"planned {hours(view.last_week_planned)} h"
    error = f'<p class="error">{escape(view.error)}</p>' if view.error else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Goals · {week_label(view.week_start)}</title>
<style>{_STYLE}</style>
</head>
<body>
<main>
<h1>Goals · {week_label(view.week_start)}</h1>
<p class="muted">Last week: {planned}, did {hours(view.last_week_done)} h</p>
{error}
<form method="post" action="/goals">
<input type="hidden" name="week_start" value="{view.week_start.isoformat()}">
{''.join(rows)}
<div class="total"><span>Study total</span><strong id="total">{total} h</strong></div>
<p class="note">{status}</p>
<button type="submit">Save goals</button>
</form>
<a href="{escape(view.dashboard_url)}">Back to dashboard</a>
</main>
<script>{_SCRIPT}</script>
</body>
</html>"""
