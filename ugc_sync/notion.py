"""Minimal Notion API client plus helpers to read/write page properties."""

from __future__ import annotations

import time
from typing import Any

from . import http

API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
_TEXT_CHUNK = 1900  # Notion caps a single rich-text item at 2000 characters


class Notion:
    def __init__(self, token: str, dry_run: bool = False, pause: float = 0.34):
        self.token = token
        self.dry_run = dry_run
        self.pause = pause  # stay under Notion's ~3 requests/second limit
        self.writes: list[tuple[str, dict]] = []

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    def query(self, database_id: str, filter: dict | None = None) -> list[dict]:
        pages: list[dict] = []
        body: dict[str, Any] = {"page_size": 100}
        if filter:
            body["filter"] = filter
        while True:
            res = http.call(
                "Notion", "POST", f"{API}/databases/{database_id}/query", json=body, headers=self._headers()
            )
            pages.extend(res.get("results", []))
            if not res.get("has_more"):
                return pages
            body["start_cursor"] = res.get("next_cursor")

    def get_page(self, page_id: str) -> dict:
        return http.call("Notion", "GET", f"{API}/pages/{page_id}", headers=self._headers())

    def update(self, page_id: str, properties: dict) -> None:
        if not properties:
            return
        self.writes.append((page_id, properties))
        if self.dry_run:
            return
        http.call(
            "Notion", "PATCH", f"{API}/pages/{page_id}", json={"properties": properties}, headers=self._headers()
        )
        if self.pause:
            time.sleep(self.pause)


# ---------- reading properties ----------

def _prop(page: dict, name: str) -> dict:
    return (page.get("properties") or {}).get(name) or {}


def _plain(items: list | None) -> str:
    return "".join((i.get("plain_text") or (i.get("text") or {}).get("content") or "") for i in (items or []))


def get_title(page: dict, name: str) -> str:
    return _plain(_prop(page, name).get("title")).strip()


def get_text(page: dict, name: str) -> str:
    return _plain(_prop(page, name).get("rich_text")).strip()


def get_number(page: dict, name: str):
    return _prop(page, name).get("number")


def get_url(page: dict, name: str) -> str:
    return (_prop(page, name).get("url") or "").strip()


def get_select(page: dict, name: str) -> str:
    sel = _prop(page, name).get("select")
    return (sel or {}).get("name") or ""


def get_date(page: dict, name: str) -> str:
    d = _prop(page, name).get("date")
    return (d or {}).get("start") or ""


def get_relation_ids(page: dict, name: str) -> list[str]:
    return [r["id"].replace("-", "") for r in (_prop(page, name).get("relation") or []) if r.get("id")]


def page_id(page: dict) -> str:
    return (page.get("id") or "").replace("-", "")


# ---------- writing properties ----------

def p_number(value) -> dict:
    return {"number": value}


def p_text(value: str | None) -> dict:
    value = value or ""
    chunks = [value[i : i + _TEXT_CHUNK] for i in range(0, len(value), _TEXT_CHUNK)] or []
    return {"rich_text": [{"type": "text", "text": {"content": c}} for c in chunks]}


def p_url(value: str | None) -> dict:
    return {"url": value or None}


def p_select(name: str | None) -> dict:
    return {"select": {"name": name} if name else None}


def p_date(start: str | None) -> dict:
    return {"date": {"start": start} if start else None}
