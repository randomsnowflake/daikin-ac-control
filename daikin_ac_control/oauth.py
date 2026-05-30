"""OAuth helpers for the Daikin ONECTA API."""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from .config import AUTHORIZE_URL, TOKEN_URL, Settings
from .http import post_form


def build_authorization_url(settings: Settings, state: str | None = None) -> str:
    params = {
        "client_id": settings.client_id,
        "redirect_uri": settings.redirect_uri,
        "response_type": "code",
        "scope": settings.scope,
        "state": state or secrets.token_urlsafe(24),
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code(settings: Settings, code: str) -> dict[str, Any]:
    token = post_form(
        TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "client_id": settings.client_id,
            "client_secret": settings.client_secret,
            "redirect_uri": settings.redirect_uri,
            "code": code,
        },
    )
    save_token(settings.token_file, token)
    return token


def refresh_token(settings: Settings, refresh_token_value: str) -> dict[str, Any]:
    token = post_form(
        TOKEN_URL,
        {
            "grant_type": "refresh_token",
            "client_id": settings.client_id,
            "client_secret": settings.client_secret,
            "refresh_token": refresh_token_value,
        },
    )
    save_token(settings.token_file, token)
    return token


def load_token(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise RuntimeError(
            f"No token file found at {path}. Run `python3 -m daikin_ac_control auth-url` first."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def save_token(path: Path, token: dict[str, Any]) -> None:
    token = dict(token)
    if "expires_in" in token:
        token["expires_at"] = int(time.time()) + int(token["expires_in"])

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(token, indent=2, sort_keys=True), encoding="utf-8")
    os.chmod(path, 0o600)


def access_token(settings: Settings) -> str:
    token = load_token(settings.token_file)
    expires_at = int(token.get("expires_at", 0))

    if expires_at <= int(time.time()) + 60:
        refresh = token.get("refresh_token")
        if not refresh:
            raise RuntimeError("Token expired and no refresh_token is available")
        token = refresh_token(settings, str(refresh))

    value = token.get("access_token")
    if not value:
        raise RuntimeError("Token file does not contain an access_token")
    return str(value)

