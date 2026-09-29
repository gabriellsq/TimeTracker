import base64
import json
import time
from collections.abc import Callable
from typing import Any

import httpx


class TimeTaggerError(Exception):
    """TimeTagger could not be reached or answered with a server error."""


class TimeTaggerAuthError(TimeTaggerError):
    """The token was rejected. Retrying cannot fix this."""


class TimeTaggerClient:
    def __init__(
        self,
        api_url: str,
        token: str,
        *,
        http: httpx.Client | None = None,
        retries: int = 3,
        backoff_seconds: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._token = token
        self._http = http or httpx.Client(timeout=30)
        self._retries = retries
        self._backoff_seconds = backoff_seconds
        self._sleep = sleep

    def __enter__(self) -> "TimeTaggerClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._http.close()

    def get_updates(self, since: float) -> dict[str, Any]:
        return self._request("GET", "updates", params={"since": since}, token=self._token).json()

    def put_records(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        return self._request("PUT", "records", json=records, token=self._token).json()

    def login(self, username: str, password: str) -> str:
        payload = {"method": "usernamepassword", "username": username, "password": password}
        body = base64.b64encode(json.dumps(payload).encode())
        return self._request("POST", "bootstrap_authentication", content=body).json()["token"]

    def get_api_token(self, webtoken: str) -> str:
        return self._request("GET", "apitoken", token=webtoken).json()["token"]

    def _request(self, method: str, path: str, *, token: str | None = None, **kwargs: Any) -> httpx.Response:
        headers = {"authtoken": token} if token else {}
        last_error: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                response = self._http.request(method, f"{self._api_url}/{path}", headers=headers, **kwargs)
            except httpx.TransportError as exc:
                last_error = exc
            else:
                if response.status_code in (401, 403):
                    raise TimeTaggerAuthError(f"HTTP {response.status_code}: {response.text[:200]}")
                if response.status_code < 500:
                    response.raise_for_status()
                    return response
                last_error = TimeTaggerError(f"HTTP {response.status_code}: {response.text[:200]}")
            if attempt < self._retries:
                self._sleep(self._backoff_seconds * 2**attempt)
        raise TimeTaggerError(f"TimeTagger failed after {self._retries + 1} attempts: {last_error}") from last_error
