"""One-time step per account: turn the code a creator sends you into stored access.

Run from GitHub: Actions → "Connect an account" → paste the code → Run workflow.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from . import crypto, instagram, youtube
from . import notion as n
from .config import TIMEZONE, Config, ConfigError
from .http import ApiError
from .links import PLATFORM_INSTAGRAM, PLATFORM_YOUTUBE
from .sync import (
    A_AUTH, A_CONNECTION, A_EXPIRES, A_FOLLOWERS, A_HANDLE, A_LINK, A_NOTE, A_PID, A_PLATFORM, A_SYNCED, CONNECTED,
)

_PLATFORMS = {"ig": PLATFORM_INSTAGRAM, "yt": PLATFORM_YOUTUBE}


class ConnectError(Exception):
    pass


def parse_connection_code(value: str) -> tuple[str, str, str]:
    value = (value or "").strip().strip('"').strip("'")
    parts = value.split("|", 2)
    if len(parts) != 3:
        raise ConnectError(
            "That doesn't look like a connection code. It should look like ig|1234abcd…|AQB… — "
            "ask the creator to copy the whole code from the approval page."
        )
    prefix, page_id, code = parts[0].lower(), parts[1].replace("-", ""), parts[2].strip()
    if prefix not in _PLATFORMS:
        raise ConnectError("The code should start with ig| or yt|")
    if not re.fullmatch(r"[0-9a-fA-F]{32}", page_id):
        raise ConnectError("The account part of the code is damaged — ask the creator to copy it again")
    if not code:
        raise ConnectError("The code is empty — ask the creator to approve again")
    return _PLATFORMS[prefix], page_id, code


def _clean(handle: str) -> str:
    return handle.strip().lstrip("@").lower()


def run(cfg: Config, connection_code: str, notion: n.Notion | None = None) -> str:
    cfg.require("notion_token", "passphrase", "redirect_uri")
    platform, page_id, code = parse_connection_code(connection_code)
    notion = notion or n.Notion(cfg.notion_token)
    page = notion.get_page(page_id)
    handle = n.get_title(page, A_HANDLE)
    if n.get_select(page, A_PLATFORM) != platform:
        raise ConnectError(f"{handle} is not set up as {platform} in Notion")
    now = datetime.now(ZoneInfo(TIMEZONE)).replace(microsecond=0)
    props: dict = {}

    if platform == PLATFORM_INSTAGRAM:
        cfg.require("ig_app_id", "ig_app_secret")
        tok = instagram.exchange_code(cfg.ig_app_id, cfg.ig_app_secret, cfg.redirect_uri, code)
        prof = instagram.InstagramAccount(tok["access_token"], cfg.ig_api_version).profile()
        username = prof.get("username") or ""
        props[A_AUTH] = n.p_text(
            crypto.encrypt({"access_token": tok["access_token"], "expires_at": tok["expires_at"]}, cfg.passphrase)
        )
        props[A_EXPIRES] = n.p_date(tok["expires_at"][:10])
        props[A_PID] = n.p_text(prof.get("user_id") or tok.get("user_id") or "")
        if prof.get("followers") is not None:
            props[A_FOLLOWERS] = n.p_number(prof["followers"])
        note = f"Connected as @{username} on {now:%d %b %Y}"
        if username and _clean(username) != _clean(handle):
            note += f" ⚠ Notion says {handle} — check this is the right account"
        label = f"@{username}" if username else handle
    else:
        cfg.require("google_client_id", "google_client_secret")
        refresh_token = youtube.exchange_code(cfg.google_client_id, cfg.google_client_secret, cfg.redirect_uri, code)
        token = youtube.access_token(cfg.google_client_id, cfg.google_client_secret, refresh_token)
        ch = youtube.YouTubeClient(access_token=token).my_channel()
        props[A_AUTH] = n.p_text(crypto.encrypt({"refresh_token": refresh_token}, cfg.passphrase))
        props[A_PID] = n.p_text(ch.get("id") or "")
        if ch.get("subscribers") is not None:
            props[A_FOLLOWERS] = n.p_number(ch["subscribers"])
        note = f"Connected to channel “{ch.get('title')}” on {now:%d %b %Y}"
        label = ch.get("title") or handle

    props[A_CONNECTION] = n.p_select(CONNECTED)
    props[A_LINK] = n.p_url(None)
    props[A_NOTE] = n.p_text(note)
    props[A_SYNCED] = n.p_date(now.isoformat())
    notion.update(page_id, props)
    return f"✅ {handle} is connected ({platform}: {label}). Stats will sync on the next daily run."


def _code_from_github() -> str:
    path = os.environ.get("GITHUB_EVENT_PATH")
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            event = json.load(fh)
        value = ((event.get("inputs") or {}).get("connection_code") or "").strip()
        if value:
            return value
    return (os.environ.get("CONNECTION_CODE") or (sys.argv[1] if len(sys.argv) > 1 else "")).strip()


def main() -> int:
    try:
        message = run(Config.from_env(), _code_from_github())
    except (ConfigError, ConnectError) as exc:
        print(f"❌ {exc}")
        return 2
    except ApiError as exc:
        hint = ""
        if exc.service in ("Instagram", "Google") and ("expired" in exc.message.lower() or "used" in exc.message.lower()
                                                       or "invalid" in exc.message.lower()):
            hint = " The code may have expired or already been used — ask the creator to open the Connect Link again."
        print(f"❌ {exc.service} said: {exc.message}.{hint}")
        return 1
    print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
