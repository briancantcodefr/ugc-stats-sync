"""Small HTTP helper with retries. Error messages never include URLs or tokens."""

from __future__ import annotations

import time
from typing import Any, Callable

import requests

RETRY_STATUS = {429, 500, 502, 503, 504}

# Swappable for tests: a callable with the same signature as requests.request.
transport: Callable[..., Any] = requests.request
sleep: Callable[[float], None] = time.sleep


class ApiError(Exception):
    def __init__(self, service: str, status: int, message: str, code: str | int | None = None):
        self.service = service
        self.status = status
        self.code = code
        self.message = message
        super().__init__(f"{service} error {status}: {message}")

    @property
    def is_auth_error(self) -> bool:
        text = (self.message or "").lower()
        return (
            self.status == 401
            or self.code in (190, "invalid_grant", "unauthorized_client")
            or "invalid_grant" in text
            or "access token" in text
            or "session has expired" in text
            or "error validating" in text
        )


def _extract_error(resp: Any) -> tuple[str, Any]:
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001 - any non-JSON body
        return (resp.text or "")[:200], None
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            msg = err.get("message") or err.get("error_user_msg") or str(err)
            return str(msg)[:300], err.get("code") or err.get("status")
        if isinstance(err, str):
            return str(body.get("error_description") or body.get("error_message") or err)[:300], err
        if body.get("message"):
            return str(body["message"])[:300], body.get("code")
        if body.get("error_message"):
            return str(body["error_message"])[:300], body.get("code")
    return str(body)[:200], None


def call(
    service: str,
    method: str,
    url: str,
    *,
    params: dict | None = None,
    data: dict | None = None,
    json: Any = None,
    headers: dict | None = None,
    timeout: int = 30,
    retries: int = 3,
) -> Any:
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            resp = transport(
                method, url, params=params, data=data, json=json, headers=headers, timeout=timeout
            )
        except requests.RequestException as exc:  # network trouble
            last_exc = exc
            if attempt < retries - 1:
                sleep(2 ** attempt)
                continue
            raise ApiError(service, 0, f"network error ({type(exc).__name__})") from None
        status = resp.status_code
        if status in RETRY_STATUS and attempt < retries - 1:
            wait = 2 ** attempt
            retry_after = getattr(resp, "headers", {}) or {}
            try:
                wait = max(wait, float(retry_after.get("Retry-After", 0)))
            except (TypeError, ValueError):
                pass
            sleep(min(wait, 30))
            continue
        if status >= 400:
            message, code = _extract_error(resp)
            raise ApiError(service, status, message, code)
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            raise ApiError(service, status, "response was not JSON") from None
    raise ApiError(service, 0, f"request failed ({type(last_exc).__name__})")
