"""Simula cada senal con el simulador de `reporter/setups.py` (100 $ x5, mitad en TP1 y mitad en TP2)."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "reporter"))
import setups  # noqa: E402

COLS = ["open_time", "open", "high", "low", "close"]


def run(d: pd.DataFrame, signals: list[dict], hold_hours: float = 12, fee: float = setups.FEE,
        margin: float = setups.MARGIN, leverage: float = setups.LEVERAGE) -> tuple[pd.DataFrame, dict]:
    """Devuelve (operaciones cerradas con entrada, conteo). No cuenta las que siguen abiertas al final de los datos."""
    notional = margin * leverage
    trades, counts = [], {"signals": 0, "no_fill": 0, "unfinished": 0}
    for s in signals:
        counts["signals"] += 1
        n = int((s["entry_hours"] + hold_hours) * 4) + 2
        sub = d.iloc[s["i"] + 1: s["i"] + 1 + n][COLS]
        res = setups.simulate(s, sub, margin, leverage, fee, entry_hours=s["entry_hours"], hold_hours=hold_hours)
        if res["status"] != "closed":
            counts["unfinished"] += 1
            continue
        if res["outcome"] == "no_fill":
            counts["no_fill"] += 1
            continue
        risk = notional * s["stop_dist"] / s["entry"]
        trades.append({"coin": s["coin"], "strategy": s["strategy"], "regime": s["regime"], "side": s["side"],
                       "signal_ts": s["created_ts"], "outcome": res["outcome"], "pnl": res["pnl"],
                       "risk_usd": risk, "r": res["pnl"] / risk})
    df = pd.DataFrame(trades)
    if len(df):
        ts = pd.to_datetime(df["signal_ts"]).dt.tz_localize(None)
        df["month"] = ts.dt.strftime("%Y-%m")
        df["quarter"] = ts.dt.to_period("Q").astype(str)
    return df, counts
