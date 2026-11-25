"""Daily job: refresh account access, then pull stats for recent videos into Notion.

Run with:  python -m ugc_sync.sync
Logs only show counts and handles — never tokens or numbers — because GitHub
Actions logs on a public repository are public.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import crypto, instagram, youtube
from . import notion as n
from .config import TIMEZONE, Config, ConfigError
from .http import ApiError
from .links import PLATFORM_INSTAGRAM, PLATFORM_YOUTUBE, connect_link

# Notion property names (must match the tracker)
A_HANDLE, A_PLATFORM, A_STATUS = "Handle", "Platform", "Account Status"
A_CONNECTION, A_LINK, A_PID = "Connection", "Connect Link", "Platform ID"
A_AUTH, A_EXPIRES, A_SYNCED, A_NOTE, A_FOLLOWERS = (
    "Auth (encrypted)", "Token Expires", "Last Synced", "Sync Note", "Current Followers",
)
L_DATE, L_ACCOUNT, L_LINK, L_STATUS = "Date Posted", "Account", "Video Link", "Posting Status"
L_VIEWS, L_LIKES, L_COMMENTS, L_SHARES, L_SAVES = "Views", "Likes", "Comments", "Shares", "Saves"
L_FOLLOWERS, L_LENGTH, L_WATCH = "Followers Gained", "Video Length (sec)", "Avg Watch Time (sec)"
L_UPDATED, L_SYNC = "Stats Updated", "Sync Status"

CONNECTED, NOT_CONNECTED, RECONNECT = "Connected", "Not connected", "Needs reconnect"
REFRESH_WHEN_DAYS_LEFT = 10


@dataclass
class AccountCtx:
    page_id: str
    handle: str
    platform: str
    ig: instagram.InstagramAccount | None = None
    yt: youtube.YouTubeClient | None = None
    channel_id: str | None = None
    public_only: bool = False
    problem: str | None = None  # why stats can't be pulled


@dataclass
class Summary:
    accounts: dict = field(default_factory=lambda: {CONNECTED: 0, NOT_CONNECTED: 0, RECONNECT: 0, "public": 0})
    videos_checked: int = 0
    videos_updated: int = 0
    issues: list = field(default_factory=list)


def _now_local() -> datetime:
    return datetime.now(ZoneInfo(TIMEZONE)).replace(microsecond=0)


def _iso_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------- accounts ----------------

def prepare_account(cfg: Config, notion: n.Notion, page: dict, now: datetime, summary: Summary) -> AccountCtx:
    pid = n.page_id(page)
    ctx = AccountCtx(pid, n.get_title(page, A_HANDLE) or "(no handle)", n.get_select(page, A_PLATFORM))
    props: dict = {}
    auth_blob = n.get_text(page, A_AUTH)
    link = connect_link(cfg, ctx.platform, pid) if ctx.platform in (PLATFORM_INSTAGRAM, PLATFORM_YOUTUBE) else None

    def not_connected(note: str, status: str = NOT_CONNECTED):
        props[A_CONNECTION] = n.p_select(status)
        if link and n.get_url(page, A_LINK) != link:
            props[A_LINK] = n.p_url(link)
        props[A_NOTE] = n.p_text(note)

    try:
        if ctx.platform == PLATFORM_INSTAGRAM:
            if not auth_blob:
                ctx.problem = "Account not connected yet — send the creator the Connect Link"
                not_connected("Waiting for the creator to approve stats access (send them the Connect Link).")
            else:
                auth = crypto.decrypt(auth_blob, cfg.passphrase)
                expires = _iso_utc(auth.get("expires_at"))
                if expires and expires <= now:
                    raise ApiError("Instagram", 401, "access expired", 190)
                if expires and expires - now < timedelta(days=REFRESH_WHEN_DAYS_LEFT):
                    auth.update(instagram.refresh(auth["access_token"]))
                    props[A_AUTH] = n.p_text(crypto.encrypt(auth, cfg.passphrase))
                    props[A_EXPIRES] = n.p_date(auth["expires_at"][:10])
                ctx.ig = instagram.InstagramAccount(auth["access_token"], cfg.ig_api_version)
                prof = ctx.ig.profile()
                props[A_CONNECTION] = n.p_select(CONNECTED)
                if prof.get("followers") is not None:
                    props[A_FOLLOWERS] = n.p_number(prof["followers"])
                if prof.get("user_id") and n.get_text(page, A_PID) != prof["user_id"]:
                    props[A_PID] = n.p_text(prof["user_id"])
                if n.get_url(page, A_LINK):
                    props[A_LINK] = n.p_url(None)
                props[A_NOTE] = n.p_text(f"OK — @{prof.get('username') or ctx.handle.lstrip('@')}")

        elif ctx.platform == PLATFORM_YOUTUBE:
            if auth_blob:
                if not cfg.youtube_oauth_ready:
                    raise ConfigError("GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / REDIRECT_URI are missing")
                auth = crypto.decrypt(auth_blob, cfg.passphrase)
                token = youtube.access_token(cfg.google_client_id, cfg.google_client_secret, auth["refresh_token"])
                ctx.yt = youtube.YouTubeClient(access_token=token)
                ch = ctx.yt.my_channel()
                ctx.channel_id = ch["id"]
                props[A_CONNECTION] = n.p_select(CONNECTED)
                if ch.get("subscribers") is not None:
                    props[A_FOLLOWERS] = n.p_number(ch["subscribers"])
                if ch.get("id") and n.get_text(page, A_PID) != ch["id"]:
                    props[A_PID] = n.p_text(ch["id"])
                if n.get_url(page, A_LINK):
                    props[A_LINK] = n.p_url(None)
                props[A_NOTE] = n.p_text(f"OK — {ch.get('title') or 'channel'}")
            elif cfg.youtube_api_key:
                ctx.yt = youtube.YouTubeClient(api_key=cfg.youtube_api_key)
                ctx.public_only = True
                ctx.channel_id = n.get_text(page, A_PID) or None
                try:
                    ch = ctx.yt.channel_by_handle(ctx.handle)
                    if ch:
                        ctx.channel_id = ch["id"]
                        if ch.get("subscribers") is not None:
                            props[A_FOLLOWERS] = n.p_number(ch["subscribers"])
                        if n.get_text(page, A_PID) != ch["id"]:
                            props[A_PID] = n.p_text(ch["id"])
                except ApiError:
                    pass  # handle lookup is a nice-to-have
                not_connected(
                    "Public stats only (views, likes, comments, length). Send the Connect Link for shares, "
                    "subscribers gained and watch time."
                )
            else:
                ctx.problem = "Account not connected yet — send the creator the Connect Link"
                not_connected("Waiting for the creator to approve stats access (send them the Connect Link).")
        else:
            ctx.problem = "Account has no Platform set"
            props[A_NOTE] = n.p_text("Set Platform to Instagram or YouTube Shorts.")

    except (ApiError, crypto.CryptoError, KeyError) as exc:
        auth_problem = isinstance(exc, (crypto.CryptoError, KeyError)) or (
            isinstance(exc, ApiError) and exc.is_auth_error
        )
        ctx.ig = ctx.yt = None
        ctx.public_only = False
        if auth_problem:
            ctx.problem = "Access expired or was removed — creator needs to reconnect"
            not_connected(f"Needs reconnect: {_short(exc)}. Send the creator the Connect Link again.", RECONNECT)
        else:
            ctx.problem = f"Temporary error: {_short(exc)}"
            props[A_NOTE] = n.p_text(f"Sync error (will retry tomorrow): {_short(exc)}")
            summary.issues.append(f"{ctx.handle}: {_short(exc)}")

    props[A_SYNCED] = n.p_date(now.isoformat())
    notion.update(pid, props)

    status = None
    if A_CONNECTION in props:
        status = props[A_CONNECTION]["select"]["name"] if props[A_CONNECTION]["select"] else None
    if ctx.public_only:
        summary.accounts["public"] += 1
    elif status in summary.accounts:
        summary.accounts[status] += 1
    return ctx


def _short(exc: Exception) -> str:
    msg = getattr(exc, "message", None) or str(exc)
    return msg.strip().rstrip(".")[:160]


# ---------------- videos ----------------

def _stat_props(stats: dict, mapping: dict) -> dict:
    props = {}
    for key, prop in mapping.items():
        value = stats.get(key)
        if isinstance(value, (int, float)):
            props[prop] = n.p_number(value)
    return props


def _set_status(notion: n.Notion, row: dict, text: str, summary: Summary) -> None:
    if n.get_text(row, L_SYNC) != text:
        notion.update(n.page_id(row), {L_SYNC: n.p_text(text)})


def sync_videos(cfg: Config, notion: n.Notion, accounts: dict[str, AccountCtx], now: datetime, summary: Summary):
    since_date = (now - timedelta(days=cfg.lookback_days)).date()
    since_dt = datetime.combine(since_date, datetime.min.time(), tzinfo=now.tzinfo) - timedelta(days=2)
    today = now.date().isoformat()
    stamp = now.strftime("%d %b %H:%M")
    rows = notion.query(cfg.log_db, filter={"property": L_DATE, "date": {"on_or_after": since_date.isoformat()}})

    yt_jobs: dict[str, list[tuple[dict, str]]] = {}
    for row in rows:
        if n.get_select(row, L_STATUS) == "Missed":
            continue
        posted = n.get_date(row, L_DATE)
        if not posted or posted[:10] < since_date.isoformat():
            continue
        summary.videos_checked += 1
        link = n.get_url(row, L_LINK)
        acct_ids = n.get_relation_ids(row, L_ACCOUNT)
        ctx = accounts.get(acct_ids[0]) if acct_ids else None
        if not link:
            _set_status(notion, row, "Add the video link to sync stats", summary)
            continue
        if not ctx:
            _set_status(notion, row, "Pick an Account to sync stats", summary)
            continue
        if ctx.problem and not (ctx.ig or ctx.yt):
            _set_status(notion, row, ctx.problem, summary)
            continue

        if ctx.platform == PLATFORM_INSTAGRAM:
            code = instagram.shortcode_from_url(link)
            if not code:
                _set_status(notion, row, "This isn't an Instagram post/reel link", summary)
                continue
            try:
                media = ctx.ig.find_media(code, since_dt)
                if not media:
                    _set_status(notion, row, f"Not found on {ctx.handle} — check the link and Account", summary)
                    continue
                stats = ctx.ig.media_stats(media["id"])
            except ApiError as exc:
                _set_status(notion, row, f"Instagram error: {_short(exc)}", summary)
                summary.issues.append(f"{ctx.handle}: {_short(exc)}")
                continue
            props = _stat_props(
                stats,
                {"views": L_VIEWS, "likes": L_LIKES, "comments": L_COMMENTS, "shares": L_SHARES,
                 "saves": L_SAVES, "avg_watch_sec": L_WATCH},
            )
            props[L_UPDATED] = n.p_date(today)
            props[L_SYNC] = n.p_text(f"✅ Synced {stamp}")
            notion.update(n.page_id(row), props)
            summary.videos_updated += 1

        elif ctx.platform == PLATFORM_YOUTUBE:
            vid = youtube.video_id_from_url(link)
            if not vid:
                _set_status(notion, row, "This isn't a YouTube video/Shorts link", summary)
                continue
            yt_jobs.setdefault(ctx.page_id, []).append((row, vid))

    for acct_id, jobs in yt_jobs.items():
        ctx = accounts[acct_id]
        try:
            found = ctx.yt.videos([vid for _, vid in jobs])
        except ApiError as exc:
            for row, _ in jobs:
                _set_status(notion, row, f"YouTube error: {_short(exc)}", summary)
            summary.issues.append(f"{ctx.handle}: {_short(exc)}")
            continue
        for row, vid in jobs:
            video = found.get(vid)
            if not video:
                _set_status(notion, row, "Video not found (private, deleted or wrong link)", summary)
                continue
            if ctx.channel_id and video.get("channel_id") and video["channel_id"] != ctx.channel_id:
                _set_status(notion, row, f"This video is on a different channel than {ctx.handle}", summary)
                continue
            stats = dict(video)
            note = ""
            if ctx.yt.has_analytics:
                try:
                    a = ctx.yt.analytics(vid, video.get("published") or since_date.isoformat(), today)
                except ApiError as exc:
                    a = {}
                    note = f" (analytics unavailable: {_short(exc)})"
                if a:
                    stats["shares"] = a.get("shares")
                    stats["subs_gained"] = a.get("subscribersGained")
                    stats["avg_watch_sec"] = a.get("averageViewDuration")
                    for key in ("views", "likes", "comments"):
                        if stats.get(key) is None:
                            stats[key] = a.get(key)
                elif not note:
                    note = " (watch time appears after 1–3 days)"
            else:
                note = " (public stats only)"
            props = _stat_props(
                stats,
                {"views": L_VIEWS, "likes": L_LIKES, "comments": L_COMMENTS, "shares": L_SHARES,
                 "subs_gained": L_FOLLOWERS, "length_sec": L_LENGTH, "avg_watch_sec": L_WATCH},
            )
            props[L_UPDATED] = n.p_date(today)
            props[L_SYNC] = n.p_text(f"✅ Synced {stamp}{note}")
            notion.update(n.page_id(row), props)
            summary.videos_updated += 1


# ---------------- entry point ----------------

def run(cfg: Config, notion: n.Notion | None = None, now: datetime | None = None) -> Summary:
    cfg.require("notion_token", "passphrase")
    notion = notion or n.Notion(cfg.notion_token, dry_run=cfg.dry_run)
    now = now or _now_local()
    summary = Summary()
    accounts: dict[str, AccountCtx] = {}
    for page in notion.query(cfg.accounts_db):
        if n.get_select(page, A_STATUS) == "Paused":
            continue
        ctx = prepare_account(cfg, notion, page, now, summary)
        accounts[ctx.page_id] = ctx
    sync_videos(cfg, notion, accounts, now, summary)
    return summary


def main() -> int:
    try:
        cfg = Config.from_env()
        summary = run(cfg)
    except ConfigError as exc:
        print(f"Setup problem: {exc}")
        return 2
    except ApiError as exc:
        print(f"Could not reach {exc.service}: {exc.message}")
        return 1
    a = summary.accounts
    print(
        f"Accounts — connected: {a[CONNECTED]}, public-only YouTube: {a['public']}, "
        f"not connected: {a[NOT_CONNECTED]}, need reconnect: {a[RECONNECT]}"
    )
    print(f"Videos — checked: {summary.videos_checked}, stats updated: {summary.videos_updated}")
    for issue in summary.issues:
        print(f"  ⚠ {issue}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
