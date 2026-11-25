"""Builds the approval ('Connect') link a creator opens once per account."""

from __future__ import annotations

from . import instagram, youtube
from .config import Config

PLATFORM_INSTAGRAM = "Instagram"
PLATFORM_YOUTUBE = "YouTube Shorts"
PREFIX = {PLATFORM_INSTAGRAM: "ig", PLATFORM_YOUTUBE: "yt"}


def state_for(platform: str, account_page_id: str) -> str:
    return f"{PREFIX[platform]}.{account_page_id.replace('-', '')}"


def connect_link(cfg: Config, platform: str, account_page_id: str) -> str | None:
    if platform == PLATFORM_INSTAGRAM and cfg.instagram_ready:
        return instagram.authorize_url(cfg.ig_app_id, cfg.redirect_uri, state_for(platform, account_page_id))
    if platform == PLATFORM_YOUTUBE and cfg.youtube_oauth_ready:
        return youtube.authorize_url(cfg.google_client_id, cfg.redirect_uri, state_for(platform, account_page_id))
    return None
