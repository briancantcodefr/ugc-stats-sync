"""Instagram API with Instagram Login (no Facebook Page needed).

Requires each account to be an Instagram *professional* account (Creator or Business).
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from . import http
from .http import ApiError

AUTH_URL = "https://www.instagram.com/oauth/authorize"
TOKEN_URL = "https://api.instagram.com/oauth/access_token"
GRAPH = "https://graph.instagram.com"
SCOPES = ["instagram_business_basic", "instagram_business_manage_insights"]

# Requested in one call; if Instagram rejects the batch (e.g. a metric that doesn't apply
# to a photo post), each metric is retried on its own.
INSIGHT_METRICS = ["views", "reach", "likes", "comments", "shares", "saved", "ig_reels_avg_watch_time"]

_SHORTCODE_RE = re.compile(r"instagram\.com/(?:[A-Za-z0-9_.]+/)?(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)", re.I)


def shortcode_from_url(url: str) -> str | None:
    m = _SHORTCODE_RE.search(url or "")
    return m.group(1) if m else None


def authorize_url(app_id: str, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": app_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": ",".join(SCOPES),
        "state": state,
        "force_reauth": "true",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def _expires_at(seconds) -> str:
    try:
        secs = int(seconds)
    except (TypeError, ValueError):
        secs = 60 * 24 * 3600
    return (datetime.now(timezone.utc) + timedelta(seconds=secs)).replace(microsecond=0).isoformat()


def exchange_code(app_id: str, app_secret: str, redirect_uri: str, code: str) -> dict:
    """Turns the one-time code from the approval page into a 60-day access token."""
    code = code.split("#")[0].strip()
    short = http.call(
        "Instagram",
        "POST",
        TOKEN_URL,
        data={
            "client_id": app_id,
            "client_secret": app_secret,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
            "code": code,
        },
    )
    if isinstance(short.get("data"), list) and short["data"]:
        short = short["data"][0]
    short_token = short.get("access_token")
    if not short_token:
        raise ApiError("Instagram", 0, "no access token returned for this code")
    long = http.call(
        "Instagram",
        "GET",
        f"{GRAPH}/access_token",
        params={"grant_type": "ig_exchange_token", "client_secret": app_secret, "access_token": short_token},
    )
    return {
        "access_token": long["access_token"],
        "expires_at": _expires_at(long.get("expires_in")),
        "user_id": str(short.get("user_id") or ""),
    }


def refresh(access_token: str) -> dict:
    """Extends a long-lived token by another 60 days (token must be at least 24h old)."""
    res = http.call(
        "Instagram",
        "GET",
        f"{GRAPH}/refresh_access_token",
        params={"grant_type": "ig_refresh_token", "access_token": access_token},
    )
    return {"access_token": res["access_token"], "expires_at": _expires_at(res.get("expires_in"))}


def _metric_value(item: dict):
    values = item.get("values")
    if isinstance(values, list) and values:
        return values[0].get("value")
    total = item.get("total_value")
    if isinstance(total, dict):
        return total.get("value")
    return None


class InstagramAccount:
    def __init__(self, access_token: str, version: str = "v25.0"):
        self.token = access_token
        self.base = f"{GRAPH}/{version}"
        self._media: list[dict] | None = None

    def _get(self, path_or_url: str, params: dict | None = None) -> dict:
        url = path_or_url if path_or_url.startswith("http") else f"{self.base}/{path_or_url}"
        params = dict(params or {})
        if "access_token=" not in url:
            params["access_token"] = self.token
        return http.call("Instagram", "GET", url, params=params)

    def profile(self) -> dict:
        res = self._get("me", {"fields": "user_id,username,followers_count"})
        return {
            "user_id": str(res.get("user_id") or res.get("id") or ""),
            "username": res.get("username") or "",
            "followers": res.get("followers_count"),
        }

    def recent_media(self, since: datetime, max_pages: int = 6) -> list[dict]:
        """Media posted on/after `since` (cached per run)."""
        if self._media is not None:
            return self._media
        items: list[dict] = []
        res = self._get("me/media", {"fields": "id,permalink,timestamp,media_product_type", "limit": 50})
        for _ in range(max_pages):
            batch = res.get("data") or []
            items.extend(batch)
            oldest = _parse_ts(batch[-1].get("timestamp")) if batch else None
            next_url = (res.get("paging") or {}).get("next")
            if not next_url or (oldest and oldest < since):
                break
            res = self._get(next_url)
        self._media = items
        return items

    def find_media(self, shortcode: str, since: datetime) -> dict | None:
        for item in self.recent_media(since):
            if shortcode_from_url(item.get("permalink") or "") == shortcode:
                return item
        return None

    def media_stats(self, media_id: str) -> dict:
        fields = self._get(media_id, {"fields": "like_count,comments_count,media_product_type"})
        metrics: dict = {}
        try:
            res = self._get(f"{media_id}/insights", {"metric": ",".join(INSIGHT_METRICS)})
            for item in res.get("data") or []:
                metrics[item.get("name")] = _metric_value(item)
        except ApiError as exc:
            if exc.is_auth_error:
                raise
            for metric in INSIGHT_METRICS:
                try:
                    res = self._get(f"{media_id}/insights", {"metric": metric})
                    for item in res.get("data") or []:
                        metrics[item.get("name")] = _metric_value(item)
                except ApiError as inner:
                    if inner.is_auth_error:
                        raise
                    continue
                time.sleep(0.1)
        avg_ms = metrics.get("ig_reels_avg_watch_time")
        return {
            "views": metrics.get("views"),
            "likes": metrics.get("likes", fields.get("like_count")),
            "comments": metrics.get("comments", fields.get("comments_count")),
            "shares": metrics.get("shares"),
            "saves": metrics.get("saved"),
            "avg_watch_sec": round(avg_ms / 1000, 1) if isinstance(avg_ms, (int, float)) else None,
        }


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
