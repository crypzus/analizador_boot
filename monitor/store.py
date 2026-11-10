"""Almacenamiento SQLite (solo lectura/escritura local)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS items(
  key TEXT PRIMARY KEY, source TEXT, kind TEXT, uid TEXT, published TEXT, seen TEXT,
  title TEXT, url TEXT, text TEXT, score INTEGER, priority TEXT, tags TEXT, coins TEXT,
  cluster_id INTEGER, is_dup INTEGER DEFAULT 0, seeded INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_items_seen ON items(seen);
CREATE TABLE IF NOT EXISTS market(
  ts TEXT, symbol TEXT, mark REAL, funding REAL, oi_usd REAL, oi_chg_1h REAL, oi_chg_24h REAL,
  ls_ratio REAL, chg_1h REAL, chg_24h REAL);
CREATE TABLE IF NOT EXISTS fng(ts TEXT, value INTEGER, label TEXT);
CREATE TABLE IF NOT EXISTS alerts(ts TEXT, level TEXT, source TEXT, title TEXT, url TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS alert_log(key TEXT PRIMARY KEY, ts TEXT);
CREATE TABLE IF NOT EXISTS health(source TEXT PRIMARY KEY, last_ok TEXT, last_error TEXT, fails INTEGER DEFAULT 0);
"""


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Store:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def is_empty(self) -> bool:
        return self.db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0

    def has_item(self, key: str) -> bool:
        return self.db.execute("SELECT 1 FROM items WHERE key=?", (key,)).fetchone() is not None

    def recent_titles(self, hours: int = 24) -> list[tuple[int, str, str]]:
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = self.db.execute("SELECT cluster_id, title, source FROM items WHERE seen>=? AND cluster_id IS NOT NULL", (since,))
        return [(r[0], r[1], r[2]) for r in rows]

    def next_cluster(self) -> int:
        return (self.db.execute("SELECT COALESCE(MAX(cluster_id),0) FROM items").fetchone()[0]) + 1

    def add_item(self, it: dict, cls: dict, cluster_id: int, is_dup: bool, seeded: bool) -> None:
        key = f"{it['source']}:{it['uid']}"
        self.db.execute(
            "INSERT OR IGNORE INTO items(key,source,kind,uid,published,seen,title,url,text,score,priority,tags,coins,cluster_id,is_dup,seeded)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (key, it["source"], it["kind"], it["uid"], it.get("published"), now(), it["title"], it.get("url"), it.get("text"),
             cls["score"], cls["priority"], ",".join(cls["tags"]), ",".join(cls["coins"]), cluster_id, int(is_dup), int(seeded)))
        self.db.commit()

    def cluster_size(self, cluster_id: int) -> int:
        return self.db.execute("SELECT COUNT(DISTINCT source) FROM items WHERE cluster_id=?", (cluster_id,)).fetchone()[0]

    def save_market(self, rows: list[dict]) -> None:
        for r in rows:
            self.db.execute("INSERT INTO market VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (now(), r["symbol"], r["mark"], r["funding"], r["oi_usd"], r["oi_chg_1h"], r["oi_chg_24h"],
                             r["ls_ratio"], r["chg_1h"], r["chg_24h"]))
        self.db.commit()

    def save_fng(self, v: dict) -> None:
        self.db.execute("INSERT INTO fng VALUES(?,?,?)", (now(), v["value"], v["label"]))
        self.db.commit()

    def log_alert(self, level: str, source: str, title: str, url: str, note: str) -> None:
        self.db.execute("INSERT INTO alerts VALUES(?,?,?,?,?,?)", (now(), level, source, title, url, note))
        self.db.commit()

    def alerted_recently(self, key: str, hours: float) -> bool:
        r = self.db.execute("SELECT ts FROM alert_log WHERE key=?", (key,)).fetchone()
        if not r:
            return False
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return r[0] >= since

    def mark_alerted(self, key: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO alert_log VALUES(?,?)", (key, now()))
        self.db.commit()

    def health_ok(self, source: str) -> None:
        self.db.execute("INSERT INTO health(source,last_ok,fails) VALUES(?,?,0) ON CONFLICT(source) DO UPDATE SET last_ok=?, fails=0",
                        (source, now(), now()))
        self.db.commit()

    def health_fail(self, source: str, err: str) -> int:
        self.db.execute("INSERT INTO health(source,last_error,fails) VALUES(?,?,1) ON CONFLICT(source) DO UPDATE SET last_error=?, fails=fails+1",
                        (source, err[:200], err[:200]))
        self.db.commit()
        return self.db.execute("SELECT fails FROM health WHERE source=?", (source,)).fetchone()[0]
