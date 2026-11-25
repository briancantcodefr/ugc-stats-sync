"""Settings, read from environment variables (GitHub Actions secrets)."""

from __future__ import annotations

import os
from dataclasses import dataclass, fields

# Database IDs of the tracker that was set up in Notion. Override with env vars if you
# ever duplicate the tracker.
DEFAULT_ACCOUNTS_DB = "423ef5f0412881ef8501eb349727b4fd"
DEFAULT_LOG_DB = "423ef5f0412881aea244fba297077e2f"

TIMEZONE = "Asia/Kolkata"


class ConfigError(Exception):
    pass


@dataclass
class Config:
    notion_token: str = ""
    passphrase: str = ""
    accounts_db: str = DEFAULT_ACCOUNTS_DB
    log_db: str = DEFAULT_LOG_DB
    redirect_uri: str = ""
    ig_app_id: str = ""
    ig_app_secret: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    youtube_api_key: str = ""
    lookback_days: int = 14
    ig_api_version: str = "v25.0"
    dry_run: bool = False

    ENV_NAMES = {
        "notion_token": "NOTION_TOKEN",
        "passphrase": "ENCRYPTION_PASSPHRASE",
        "accounts_db": "NOTION_ACCOUNTS_DB",
        "log_db": "NOTION_LOG_DB",
        "redirect_uri": "REDIRECT_URI",
        "ig_app_id": "IG_APP_ID",
        "ig_app_secret": "IG_APP_SECRET",
        "google_client_id": "GOOGLE_CLIENT_ID",
        "google_client_secret": "GOOGLE_CLIENT_SECRET",
        "youtube_api_key": "YOUTUBE_API_KEY",
        "lookback_days": "LOOKBACK_DAYS",
        "ig_api_version": "IG_API_VERSION",
        "dry_run": "DRY_RUN",
    }

    @classmethod
    def from_env(cls, env: dict | None = None) -> "Config":
        env = os.environ if env is None else env
        cfg = cls()
        for f in fields(cls):
            name = cls.ENV_NAMES.get(f.name)
            raw = (env.get(name) or "").strip() if name else ""
            if not raw:
                continue
            if f.name == "lookback_days":
                try:
                    cfg.lookback_days = max(1, min(365, int(raw)))
                except ValueError as exc:
                    raise ConfigError("LOOKBACK_DAYS must be a whole number") from exc
            elif f.name == "dry_run":
                cfg.dry_run = raw.lower() in ("1", "true", "yes")
            elif f.name in ("accounts_db", "log_db"):
                setattr(cfg, f.name, raw.replace("-", ""))
            else:
                setattr(cfg, f.name, raw)
        if cfg.redirect_uri and not cfg.redirect_uri.endswith("/") and not cfg.redirect_uri.endswith(".html"):
            # GitHub Pages serves index.html at the folder URL; Meta and Google need an exact match,
            # so normalise to a trailing slash.
            cfg.redirect_uri += "/"
        return cfg

    def require(self, *attrs: str) -> None:
        missing = [self.ENV_NAMES[a] for a in attrs if not getattr(self, a)]
        if missing:
            raise ConfigError("Missing GitHub secret(s): " + ", ".join(missing))
        if "passphrase" in attrs and len(self.passphrase) < 16:
            raise ConfigError("ENCRYPTION_PASSPHRASE must be at least 16 characters long")

    @property
    def instagram_ready(self) -> bool:
        return bool(self.ig_app_id and self.ig_app_secret and self.redirect_uri)

    @property
    def youtube_oauth_ready(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret and self.redirect_uri)
