"""Tiny JSON HTTP helpers using only the Python standard library."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class ApiError(RuntimeError):
    """Raised when a Daikin HTTP request fails."""

    def __init__(self, message: str, status: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status = status
        self.body = body


def post_form(url: str, form: dict[str, str], timeout: int = 30) -> dict[str, Any]:
    body = urlencode(form).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    return _json_request(request, timeout)


def get_json(url: str, access_token: str, timeout: int = 30) -> Any:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "Authorization": f"Bearer {access_token}",
        },
        method="GET",
    )
    return _json_request(request, timeout)


def patch_json(url: str, access_token: str, payload: dict[str, Any], timeout: int = 30) -> Any:
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="PATCH",
    )
    return _json_request(request, timeout)


def _json_request(request: Request, timeout: int) -> Any:
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        body = _read_error_body(exc)
        detail = _error_detail(body)
        suffix = f": {detail}" if detail else ""
        raise ApiError(
            f"HTTP {exc.code} from {request.full_url}{suffix}",
            status=exc.code,
            body=body,
        ) from exc
    except (TimeoutError, URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise ApiError(f"Network error calling {request.full_url}: {reason}") from exc

    if not payload:
        return {}

    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ApiError(f"Expected JSON from {request.full_url}", body=payload) from exc


def _read_error_body(exc: HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", errors="replace")
    finally:
        exc.close()


def _error_detail(body: str) -> str | None:
    if not body:
        return None

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return body[:500]

    if not isinstance(payload, dict):
        return body[:500]

    for key in ("error_description", "errorMessage", "message", "detail", "error"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value

    return body[:500]
