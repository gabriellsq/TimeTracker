import time
import uuid

import httpx
import pytest
from testcontainers.core.container import DockerContainer

from lifelog.sources.timetagger import TimeTaggerAuthError, TimeTaggerClient, TimeTaggerSource

pytestmark = pytest.mark.integration

# test:test — the example credentials from TimeTagger's own docker-compose file.
TEST_CREDENTIALS = "test:$2a$08$0CD1NFiIbancwWsu3se1v.RNR/b7YeZd71yg3cZ/3whGlyU6Iny5i"


@pytest.fixture(scope="module")
def api_url():
    container = (
        DockerContainer("ghcr.io/almarklein/timetagger")
        .with_env("TIMETAGGER_BIND", "0.0.0.0:80")
        .with_env("TIMETAGGER_DATADIR", "/root/_timetagger")
        .with_env("TIMETAGGER_CREDENTIALS", TEST_CREDENTIALS)
        .with_exposed_ports(80)
    )
    with container:
        base = f"http://{container.get_container_host_ip()}:{container.get_exposed_port(80)}"
        deadline = time.monotonic() + 60
        while True:
            try:
                if httpx.get(f"{base}/timetagger/", timeout=2).status_code < 500:
                    break
            except httpx.TransportError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError("TimeTagger did not start within 60 s")
            time.sleep(1)
        yield f"{base}/timetagger/api/v2"


@pytest.fixture(scope="module")
def api_token(api_url):
    with TimeTaggerClient(api_url, token="") as client:
        return client.get_api_token(client.login("test", "test"))


def test_put_then_fetch_updates(api_url, api_token):
    now = int(time.time())
    done, running, hidden = (uuid.uuid4().hex[:8] for _ in range(3))
    with TimeTaggerClient(api_url, api_token) as client:
        result = client.put_records(
            [
                {"key": done, "t1": now - 3600, "t2": now - 60, "mt": now, "ds": "#study #math"},
                {"key": running, "t1": now - 600, "t2": now - 600, "mt": now, "ds": "#gym"},
                {"key": hidden, "t1": now - 7200, "t2": now - 3600, "mt": now, "ds": "HIDDEN #rest"},
            ]
        )
        assert sorted(result["accepted"]) == sorted([done, running, hidden])

        fetched = TimeTaggerSource(client).fetch(None)

    assert float(fetched.new_cursor) > 0
    by_key = {r["key"]: r for r in fetched.records}
    assert by_key[done]["st"] > 0

    source = TimeTaggerSource(client)
    assert source.to_activity(by_key[done]).tags == ("study", "math")
    assert source.to_activity(by_key[running]).ended_at is None
    assert source.to_activity(by_key[hidden]).is_deleted is True


def test_bad_token_is_an_auth_error(api_url):
    with TimeTaggerClient(api_url, "not-a-token") as client, pytest.raises(TimeTaggerAuthError):
        client.get_updates(0)
