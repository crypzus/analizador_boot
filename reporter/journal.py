"""Registro de predicciones (SQLite) y su evaluacion a 24 h."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

FMT = "%Y-%m-%dT%H:%M:%SZ"
SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY, ts TEXT, ok INTEGER, note TEXT);
CREATE TABLE IF NOT EXISTS predictions(
  id INTEGER PRIMARY KEY, run_id INTEGER, symbol TEXT, ts TEXT, price REAL, prob_up REAL, method TEXT,
  ref50 REAL, ref_mom REAL, resolved_ts TEXT, price_24h REAL, outcome INTEGER,
  brier REAL, brier_ref50 REAL, brier_mom REAL);
CREATE INDEX IF NOT EXISTS ix_pred_open ON predictions(resolved_ts, ts);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime(FMT)


def _brier(p: float, outcome: int) -> float:
    return (p - outcome) ** 2


class Journal:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def start_run(self, ts: str) -> int:
        cur = self.db.execute("INSERT INTO runs(ts, ok, note) VALUES(?,NULL,'')", (ts,))
        self.db.commit()
        return cur.lastrowid

    def end_run(self, run_id: int, ok: bool, note: str = "") -> None:
        self.db.execute("UPDATE runs SET ok=?, note=? WHERE id=?", (1 if ok else 0, note, run_id))
        self.db.commit()

    def save_prediction(self, run_id: int, symbol: str, ts: str, price: float, prob_up: float,
                        method: str, ref_mom: float = 0.5) -> int:
        cur = self.db.execute(
            "INSERT INTO predictions(run_id,symbol,ts,price,prob_up,method,ref50,ref_mom) VALUES(?,?,?,?,?,?,0.5,?)",
            (run_id, symbol, ts, price, prob_up, method, ref_mom))
        self.db.commit()
        return cur.lastrowid

    def pending(self, now: str) -> list[dict]:
        cutoff = (datetime.strptime(now, FMT) - timedelta(hours=24)).strftime(FMT)
        rows = self.db.execute(
            "SELECT id, symbol, ts, price FROM predictions WHERE resolved_ts IS NULL AND ts<=? ORDER BY ts",
            (cutoff,)).fetchall()
        return [dict(r) for r in rows]

    def resolve(self, pred_id: int, price_24h: float, now: str | None = None) -> None:
        r = self.db.execute("SELECT price, prob_up, ref50, ref_mom FROM predictions WHERE id=?", (pred_id,)).fetchone()
        outcome = 1 if price_24h > r["price"] else 0
        self.db.execute(
            "UPDATE predictions SET resolved_ts=?, price_24h=?, outcome=?, brier=?, brier_ref50=?, brier_mom=? WHERE id=?",
            (now or utcnow(), price_24h, outcome, _brier(r["prob_up"], outcome),
             _brier(r["ref50"], outcome), _brier(r["ref_mom"], outcome), pred_id))
        self.db.commit()

    def stats(self) -> dict:
        """Por moneda y global. n_runs cuenta corridas completadas; n_failed_runs las fallidas o sin terminar."""
        def agg(where: str, args: tuple = ()) -> dict:
            r = self.db.execute(
                "SELECT COUNT(*) n, AVG(brier) b, AVG(brier_ref50) b50, AVG(brier_mom) bm, "
                "AVG((prob_up>=0.5)=outcome) acc, AVG((ref_mom>=0.5)=outcome) acc_mom "
                f"FROM predictions WHERE outcome IS NOT NULL {where}", args).fetchone()
            return {"n_resolved": r["n"], "brier": r["b"], "brier_ref50": r["b50"], "brier_mom": r["bm"],
                    "accuracy": r["acc"], "accuracy_mom": r["acc_mom"]}
        out = {"global": agg("")}
        out["global"]["n_runs"] = self.db.execute("SELECT COUNT(*) FROM runs WHERE ok=1").fetchone()[0]
        out["global"]["n_failed_runs"] = self.db.execute("SELECT COUNT(*) FROM runs WHERE ok IS NOT 1").fetchone()[0]
        out["global"]["n_runs_resolved"] = self.db.execute(
            "SELECT COUNT(DISTINCT run_id) FROM predictions WHERE outcome IS NOT NULL").fetchone()[0]
        for (sym,) in self.db.execute("SELECT DISTINCT symbol FROM predictions ORDER BY symbol").fetchall():
            out[sym] = agg("AND symbol=?", (sym,))
        return out
