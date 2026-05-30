"""Configuration helpers for the Daikin ONECTA CLI."""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path

AUTHORIZE_URL = "https://idp.onecta.daikineurope.com/v1/oidc/authorize"
TOKEN_URL = "https://idp.onecta.daikineurope.com/v1/oidc/token"
API_BASE_URL = "https://api.onecta.daikineurope.com"
DEFAULT_REDIRECT_URI = "https://example.com/daikin/callback"
DEFAULT_SCOPE = "openid onecta:basic.integration offline_access"


@dataclass(frozen=True)
class Settings:
    client_id: str
    client_secret: str
    redirect_uri: str
    token_file: Path
    scope: str
    device_id: str | None


def load_settings(require_secret: bool = True) -> Settings:
    load_dotenv()

    client_id = os.environ.get("DAIKIN_CLIENT_ID", "").strip()
    client_secret = os.environ.get("DAIKIN_CLIENT_SECRET", "").strip()

    if not client_id:
        raise RuntimeError("DAIKIN_CLIENT_ID is required")
    if require_secret and not client_secret:
        raise RuntimeError("DAIKIN_CLIENT_SECRET is required")

    token_file = Path(
        os.environ.get(
            "DAIKIN_TOKEN_FILE",
            Path.home() / ".config" / "daikin-ac-control" / "tokens.json",
        )
    ).expanduser()

    return Settings(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=os.environ.get("DAIKIN_REDIRECT_URI", DEFAULT_REDIRECT_URI),
        token_file=token_file,
        scope=os.environ.get("DAIKIN_SCOPE", DEFAULT_SCOPE),
        device_id=os.environ.get("DAIKIN_DEVICE_ID") or None,
    )


def load_dotenv(path: Path | None = None) -> None:
    dotenv = path or Path.cwd() / ".env"
    if not dotenv.exists():
        return

    for line_number, raw_line in enumerate(dotenv.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key.removeprefix("export ").strip()
        if not key or key in os.environ:
            continue

        try:
            parts = shlex.split(value, comments=True, posix=True)
        except ValueError as exc:
            raise RuntimeError(f"Invalid .env syntax at {dotenv}:{line_number}: {exc}") from exc
        os.environ[key] = parts[0] if parts else ""
