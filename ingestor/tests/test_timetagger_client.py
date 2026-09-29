import base64
import json

import httpx
import pytest

from lifelog.sources.timetagger import (
    TimeTaggerAuthError,
    TimeTaggerClient,
    TimeTaggerError,
)

API = "http://tt/timetagger/api/v2"


def make_client(handler, token="tok", retries=3):
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return TimeTaggerClient(API, token, http=http, retries=retries, sleep=lambda s: None)


def test_get_updates_sends_token_and_since():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["since"] = request.url.params["since"]
        seen["token"] = request.headers["authtoken"]
        return httpx.Response(200, json={"server_time": 10.5, "reset": 0, "records": [], "settings": []})

    with make_client(handler) as client:
        data = client.get_updates(3.25)

    assert data["server_time"] == 10.5
    assert seen == {"path": "/timetagger/api/v2/updates", "since": "3.25", "token": "tok"}


def test_server_errors_are_retried_then_succeed():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, json={"server_time": 1.0, "reset": 0, "records": [], "settings": []})

    with make_client(handler) as client:
        client.get_updates(0)

    assert len(calls) == 3


def test_gives_up_after_retries():
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ConnectError("refused", request=request)

    with make_client(handler, retries=3) as client, pytest.raises(TimeTaggerError):
        client.get_updates(0)

    assert len(calls) == 4  # 1 attempt + 3 retries


def test_auth_error_is_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(401, text="bad token")

    with make_client(handler) as client, pytest.raises(TimeTaggerAuthError):
        client.get_updates(0)

    assert len(calls) == 1


def test_login_posts_base64_json_and_returns_webtoken():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["body"] = json.loads(base64.b64decode(request.content))
        return httpx.Response(200, json={"token": "web123"})

    with make_client(handler, token="") as client:
        assert client.login("me", "secret") == "web123"

    assert seen["path"] == "/timetagger/api/v2/bootstrap_authentication"
    assert seen["body"] == {"method": "usernamepassword", "username": "me", "password": "secret"}


def test_get_api_token_uses_webtoken():
    def handler(request):
        assert request.url.path == "/timetagger/api/v2/apitoken"
        assert request.headers["authtoken"] == "web123"
        return httpx.Response(200, json={"token": "api456"})

    with make_client(handler, token="") as client:
        assert client.get_api_token("web123") == "api456"


def test_put_records_sends_json_list():
    def handler(request):
        assert request.method == "PUT"
        assert request.url.path == "/timetagger/api/v2/records"
        body = json.loads(request.content)
        return httpx.Response(200, json={"accepted": [r["key"] for r in body], "failed": [], "errors": []})

    with make_client(handler) as client:
        result = client.put_records([{"key": "k1", "t1": 1, "t2": 2, "mt": 2, "ds": "#x"}])

    assert result["accepted"] == ["k1"]
