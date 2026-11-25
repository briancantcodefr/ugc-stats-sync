"""YouTube Data API (public numbers) + YouTube Analytics API (creator-approved numbers)."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlparse

from . import http
from .http import ApiError

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
DATA_API = "https://www.googleapis.com/youtube/v3"
ANALYTICS_API = "https://youtubeanalytics.googleapis.com/v2/reports"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]
ANALYTICS_METRICS = [
    "views",
    "likes",
    "comments",
    "shares",
    "subscribersGained",
    "averageViewDuration",
    "averageViewPercentage",
]

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_DURATION_RE = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$")


def video_id_from_url(url: str) -> str | None:
    if not url:
        return None
    url = url.strip()
    if _ID_RE.match(url):
        return url
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    parts = [p for p in parsed.path.split("/") if p]
    candidate = None
    if host.endswith("youtu.be") and parts:
        candidate = parts[0]
    elif "youtube.com" in host:
        if parts and parts[0] in ("shorts", "embed", "live", "v") and len(parts) > 1:
            candidate = parts[1]
        else:
            candidate = (parse_qs(parsed.query).get("v") or [None])[0]
    return candidate if candidate and _ID_RE.match(candidate) else None


def parse_duration(value: str | None) -> int | None:
    m = _DURATION_RE.match(value or "")
    if not m:
        return None
    days, hours, minutes, seconds = m.groups()
    return int(int(days or 0) * 86400 + int(hours or 0) * 3600 + int(minutes or 0) * 60 + float(seconds or 0))


def authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code(client_id: str, client_secret: str, redirect_uri: str, code: str) -> str:
    res = http.call(
        "Google",
        "POST",
        TOKEN_URL,
        data={
            "code": code.strip(),
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
    )
    refresh_token = res.get("refresh_token")
    if not refresh_token:
        raise ApiError(
            "Google",
            0,
            "Google did not return long-term access. Ask the creator to remove the app at "
            "myaccount.google.com/permissions and approve again.",
        )
    return refresh_token


def access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    res = http.call(
        "Google",
        "POST",
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )
    return res["access_token"]


def _num(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class YouTubeClient:
    """Use access_token for an approved channel, or api_key for public numbers only."""

    def __init__(self, access_token: str | None = None, api_key: str | None = None):
        if not access_token and not api_key:
            raise ValueError("YouTubeClient needs an access token or an API key")
        self.access_token = access_token
        self.api_key = api_key

    @property
    def has_analytics(self) -> bool:
        return bool(self.access_token)

    def _get(self, url: str, params: dict) -> dict:
        params = dict(params)
        headers = None
        if self.access_token:
            headers = {"Authorization": f"Bearer {self.access_token}"}
        else:
            params["key"] = self.api_key
        return http.call("YouTube", "GET", url, params=params, headers=headers)

    def my_channel(self) -> dict:
        res = self._get(f"{DATA_API}/channels", {"part": "id,snippet,statistics", "mine": "true"})
        items = res.get("items") or []
        if not items:
            raise ApiError("YouTube", 0, "this Google account has no YouTube channel")
        return _channel(items[0])

    def channel_by_handle(self, handle: str) -> dict | None:
        handle = handle.strip()
        if not handle:
            return None
        if not handle.startswith("@"):
            handle = "@" + handle
        res = self._get(f"{DATA_API}/channels", {"part": "id,snippet,statistics", "forHandle": handle})
        items = res.get("items") or []
        return _channel(items[0]) if items else None

    def videos(self, ids: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        unique = list(dict.fromkeys(i for i in ids if i))
        for start in range(0, len(unique), 50):
            batch = unique[start : start + 50]
            res = self._get(
                f"{DATA_API}/videos", {"part": "snippet,statistics,contentDetails", "id": ",".join(batch)}
            )
            for item in res.get("items") or []:
                stats = item.get("statistics") or {}
                snippet = item.get("snippet") or {}
                out[item["id"]] = {
                    "channel_id": snippet.get("channelId"),
                    "published": (snippet.get("publishedAt") or "")[:10],
                    "views": _num(stats.get("viewCount")),
                    "likes": _num(stats.get("likeCount")),
                    "comments": _num(stats.get("commentCount")),
                    "length_sec": parse_duration((item.get("contentDetails") or {}).get("duration")),
                }
        return out

    def analytics(self, video_id: str, start_date: str, end_date: str) -> dict:
        if not self.access_token:
            return {}
        res = self._get(
            ANALYTICS_API,
            {
                "ids": "channel==MINE",
                "startDate": start_date,
                "endDate": end_date,
                "metrics": ",".join(ANALYTICS_METRICS),
                "filters": f"video=={video_id}",
            },
        )
        headers = [h.get("name") for h in res.get("columnHeaders") or []]
        rows = res.get("rows") or []
        if not rows:
            return {}
        return dict(zip(headers, rows[0], strict=False))


def _channel(item: dict) -> dict:
    stats = item.get("statistics") or {}
    return {
        "id": item.get("id"),
        "title": (item.get("snippet") or {}).get("title") or "",
        "subscribers": None if stats.get("hiddenSubscriberCount") else _num(stats.get("subscriberCount")),
    }
