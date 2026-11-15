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
CREATE TABLE IF NOT EXISTS vol_forecasts(
  id INTEGER PRIMARY KEY, run_id INTEGER, symbol TEXT, as_of TEXT, r_pred REAL, r_lo REAL, r_hi REAL,
  r1 REAL, r30 REAL, resolved_ts TEXT, r_actual REAL);
CREATE TABLE IF NOT EXISTS setups(
  id INTEGER PRIMARY KEY, run_id INTEGER, symbol TEXT, created_ts TEXT, side TEXT,
  entry REAL, stop REAL, tp1 REAL, tp2 REAL, margin REAL, leverage REAL,
  status TEXT, filled_ts TEXT, closed_ts TEXT, outcome TEXT, pnl REAL);
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

    def methods(self) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT DISTINCT method FROM predictions ORDER BY method")]

    def last_run_predictions(self, method: str) -> list[dict]:
        """Predicciones de `method` en la ultima corrida que las tiene (para colgar otro metodo de la misma corrida)."""
        rid = self.db.execute("SELECT MAX(run_id) FROM predictions WHERE method=?", (method,)).fetchone()[0]
        rows = self.db.execute("SELECT run_id, symbol, ts, price, ref_mom, prob_up FROM predictions "
                               "WHERE run_id=? AND method=?", (rid, method)).fetchall()
        return [dict(r) for r in rows]

    def has_prediction(self, run_id: int, symbol: str, method: str) -> bool:
        return self.db.execute("SELECT 1 FROM predictions WHERE run_id=? AND symbol=? AND method=?",
                               (run_id, symbol, method)).fetchone() is not None

    def save_vol_forecast(self, run_id: int, symbol: str, f: dict) -> int:
        cur = self.db.execute("INSERT INTO vol_forecasts(run_id,symbol,as_of,r_pred,r_lo,r_hi,r1,r30) VALUES(?,?,?,?,?,?,?,?)",
                              (run_id, symbol, f["as_of"], f["r"], f["r_lo"], f["r_hi"], f["r1"], f["r30"]))
        self.db.commit()
        return cur.lastrowid

    def pending_vol(self, now: str) -> list[dict]:
        cutoff = (datetime.strptime(now, FMT) - timedelta(hours=24)).strftime(FMT)
        rows = self.db.execute("SELECT id, symbol, as_of FROM vol_forecasts WHERE resolved_ts IS NULL AND as_of<=? "
                               "ORDER BY as_of", (cutoff,)).fetchall()
        return [dict(r) for r in rows]

    def resolve_vol(self, fid: int, r_actual: float, now: str | None = None) -> None:
        self.db.execute("UPDATE vol_forecasts SET resolved_ts=?, r_actual=? WHERE id=?", (now or utcnow(), r_actual, fid))
        self.db.commit()

    def vol_stats(self) -> dict:
        """Errores en logs del pronostico y de dos referencias (rango de ayer y media de 30 dias), y % dentro de la banda."""
        import math
        rows = self.db.execute("SELECT r_pred, r_lo, r_hi, r1, r30, r_actual FROM vol_forecasts WHERE r_actual>0").fetchall()
        n = len(rows)
        if not n:
            return {"n": 0}
        sq = lambda a, b: (math.log(a) - math.log(b)) ** 2  # noqa: E731
        return {"n": n,
                "rmse_model": math.sqrt(sum(sq(r["r_pred"], r["r_actual"]) for r in rows) / n),
                "rmse_pers": math.sqrt(sum(sq(r["r1"], r["r_actual"]) for r in rows) / n),
                "rmse_m30": math.sqrt(sum(sq(r["r30"], r["r_actual"]) for r in rows) / n),
                "inside": sum(r["r_lo"] <= r["r_actual"] <= r["r_hi"] for r in rows) / n,
                "bias": sum(math.log(r["r_actual"]) - math.log(r["r_pred"]) for r in rows) / n}

    def save_setup(self, run_id: int, symbol: str, created_ts: str, s: dict,
                   margin: float = 100.0, leverage: float = 5) -> int:
        cur = self.db.execute(
            "INSERT INTO setups(run_id,symbol,created_ts,side,entry,stop,tp1,tp2,margin,leverage,status) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,'waiting')",
            (run_id, symbol, created_ts, s["side"], s["entry"], s["stop"], s["tp1"], s["tp2"], margin, leverage))
        self.db.commit()
        return cur.lastrowid

    def has_setup(self, run_id: int, symbol: str) -> bool:
        return self.db.execute("SELECT 1 FROM setups WHERE run_id=? AND symbol=?", (run_id, symbol)).fetchone() is not None

    def unfinished_setups(self) -> list[dict]:
        rows = self.db.execute("SELECT * FROM setups WHERE status!='closed' ORDER BY created_ts").fetchall()
        return [dict(r) for r in rows]

    def update_setup(self, setup_id: int, res: dict) -> None:
        self.db.execute("UPDATE setups SET status=?, filled_ts=?, closed_ts=?, outcome=?, pnl=? WHERE id=?",
                        (res["status"], res["filled_ts"], res["closed_ts"], res["outcome"], res["pnl"], setup_id))
        self.db.commit()

    def setup_stats(self) -> dict:
        """Por moneda y global: setups, activos, sin entrada, operados (con entrada y cerrados), ganadores, stops, TP2, P&L."""
        def agg(where: str = "", args: tuple = ()) -> dict:
            r = self.db.execute(
                "SELECT COUNT(*) n, COALESCE(SUM(status!='closed'),0) active, COALESCE(SUM(outcome='no_fill'),0) no_fill, "
                "COALESCE(SUM(status='closed' AND outcome!='no_fill'),0) traded, "
                "COALESCE(SUM(outcome!='no_fill' AND pnl>0),0) wins, "
                "COALESCE(SUM(outcome IN ('stop','tp1_stop')),0) stops, COALESCE(SUM(outcome='tp2'),0) tp2, "
                f"COALESCE(SUM(CASE WHEN outcome!='no_fill' THEN pnl END),0) total FROM setups WHERE 1=1 {where}",
                args).fetchone()
            d = dict(r)
            d["avg"] = d["total"] / d["traded"] if d["traded"] else None
            return d
        out = {"global": agg()}
        for (sym,) in self.db.execute("SELECT DISTINCT symbol FROM setups ORDER BY symbol").fetchall():
            out[sym] = agg("AND symbol=?", (sym,))
        return out

    def stats(self, method: str | None = None) -> dict:
        """Por moneda y global (opcionalmente de un solo metodo). n_runs cuenta corridas completadas."""
        mfilter, margs = ("AND method=?", (method,)) if method else ("", ())

        def agg(where: str, args: tuple = ()) -> dict:
            r = self.db.execute(
                "SELECT COUNT(*) n, AVG(brier) b, AVG(brier_ref50) b50, AVG(brier_mom) bm, "
                "AVG((prob_up>=0.5)=outcome) acc, AVG((ref_mom>=0.5)=outcome) acc_mom "
                f"FROM predictions WHERE outcome IS NOT NULL {mfilter} {where}", margs + args).fetchone()
            return {"n_resolved": r["n"], "brier": r["b"], "brier_ref50": r["b50"], "brier_mom": r["bm"],
                    "accuracy": r["acc"], "accuracy_mom": r["acc_mom"]}
        out = {"global": agg("")}
        out["global"]["n_runs"] = self.db.execute("SELECT COUNT(*) FROM runs WHERE ok=1").fetchone()[0]
        out["global"]["n_failed_runs"] = self.db.execute("SELECT COUNT(*) FROM runs WHERE ok IS NOT 1").fetchone()[0]
        out["global"]["n_runs_resolved"] = self.db.execute(
            f"SELECT COUNT(DISTINCT run_id) FROM predictions WHERE outcome IS NOT NULL {mfilter}", margs).fetchone()[0]
        for (sym,) in self.db.execute("SELECT DISTINCT symbol FROM predictions ORDER BY symbol").fetchall():
            out[sym] = agg("AND symbol=?", (sym,))
        return out
