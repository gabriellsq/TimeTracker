from datetime import UTC, datetime

import pytest

from lifelog.sources.timetagger import TimeTaggerSource

T0 = 1_790_000_000  # 2026-09-21T13:33:20Z


class FakeClient:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.since_seen = []

    def get_updates(self, since):
        self.since_seen.append(since)
        return self.responses.pop(0)


def record(**overrides):
    base = {"key": "k1", "t1": T0, "t2": T0 + 3600, "mt": T0 + 3600, "ds": "#study #math", "st": 100.5}
    return base | overrides


def test_to_activity_maps_fields_in_utc():
    a = TimeTaggerSource(FakeClient()).to_activity(record())
    assert a.source == "timetagger"
    assert a.source_id == "k1"
    assert a.started_at == datetime.fromtimestamp(T0, UTC)
    assert a.ended_at == datetime.fromtimestamp(T0 + 3600, UTC)
    assert a.source_updated_at == datetime.fromtimestamp(T0 + 3600, UTC)
    assert a.tags == ("study", "math")
    assert a.description == "#study #math"
    assert a.is_deleted is False


def test_running_record_has_no_end():
    a = TimeTaggerSource(FakeClient()).to_activity(record(t2=T0))
    assert a.ended_at is None


def test_hidden_record_is_deleted():
    a = TimeTaggerSource(FakeClient()).to_activity(record(ds="HIDDEN #study"))
    assert a.is_deleted is True
    assert a.tags == ("study",)


def test_missing_description_gives_empty_tags():
    rec = record()
    del rec["ds"]
    a = TimeTaggerSource(FakeClient()).to_activity(rec)
    assert (a.description, a.tags) == ("", ())


def test_end_before_start_is_rejected():
    with pytest.raises(ValueError, match="ends before it starts"):
        TimeTaggerSource(FakeClient()).to_activity(record(t2=T0 - 1))


def test_fetch_without_cursor_starts_from_zero():
    client = FakeClient([{"server_time": 123.25, "reset": 0, "records": [record()], "settings": []}])
    result = TimeTaggerSource(client).fetch(None)
    assert client.since_seen == [0.0]
    assert result.new_cursor == "123.25"
    assert result.records == [record()]
    assert result.reset is False


def test_fetch_uses_cursor_as_since():
    client = FakeClient([{"server_time": 200.0, "reset": 1, "records": [], "settings": []}])
    result = TimeTaggerSource(client).fetch("123.25")
    assert client.since_seen == [123.25]
    assert result.reset is True
