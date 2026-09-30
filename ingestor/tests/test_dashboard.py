import json
import re

from conftest import DB_DIR

DASHBOARD = DB_DIR.parent / "grafana" / "dashboards" / "this-week.json"


def _load():
    return json.loads(DASHBOARD.read_text(encoding="utf-8"))


def _walk(node):
    """Yield every dict and list value in a JSON tree."""
    yield node
    if isinstance(node, dict):
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def test_dashboard_queries_run_as_grafana_and_match_panels(conn):
    conn.execute(
        "INSERT INTO core.goal (week_start, subject, target_hours) "
        "VALUES ((SELECT week_start FROM mart.v_current_week), 'ds', 8)"
    )
    dashboard = _load()
    checked = 0
    conn.execute("SET ROLE grafana_ro")
    try:
        for panel in dashboard["panels"]:
            columns = set()
            first_columns = []
            for target in panel["targets"]:
                cur = conn.execute(target["rawSql"])
                names = [d.name for d in cur.description]
                columns.update(names)
                first_columns.append((target, names))
                checked += 1

            for target, names in first_columns:
                if target.get("format") == "time_series":
                    assert names[0] == "time", (panel["title"], names)

            fields = panel.get("options", {}).get("reduceOptions", {}).get("fields")
            if isinstance(fields, str) and fields.startswith("/") and fields.endswith("/"):
                pattern = fields[1:-1]
                assert any(re.search(pattern, c) for c in columns), (
                    panel["title"],
                    fields,
                    columns,
                )

            for override in panel.get("fieldConfig", {}).get("overrides", []):
                matcher = override["matcher"]
                if matcher["id"] == "byName":
                    assert matcher["options"] in columns, (panel["title"], matcher, columns)
    finally:
        conn.execute("RESET ROLE")
    assert checked >= 1


def test_dashboard_uses_plain_hour_suffix():
    dashboard = _load()
    units = [
        node["unit"]
        for node in _walk(dashboard)
        if isinstance(node, dict) and "unit" in node
    ]
    assert units, "expected hour fields with a unit"
    assert "h" not in units
    assert all(u == "suffix: h" for u in units)


def test_dashboard_is_pinned_to_vancouver():
    dashboard = _load()
    assert dashboard["timezone"] == "America/Vancouver"
    assert dashboard["weekStart"] == "monday"
