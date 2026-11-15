"""Estadisticas por celda (estrategia x regimen), por trimestre y con ventanas moviles (walk-forward).

Criterio de exito FIJADO ANTES de ver resultados (`verdict`): una celda pasa solo si tiene >= 100 operaciones,
resultado medio por operacion > 0 despues de comisiones, limite inferior del intervalo de confianza del 95 % > 0
(remuestreo por meses, porque las operaciones de un mismo mes estan correlacionadas) y es positiva en >= 2/3
de los trimestres con al menos 5 operaciones.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MIN_TRADES, MIN_Q_TRADES, Q_SHARE = 100, 5, 2 / 3


def boot_ci(df: pd.DataFrame, col: str = "pnl", n: int = 2000, seed: int = 1, level: float = 95.0) -> tuple[float, float]:
    """IC (`level` %, por defecto 95) de la media de `col` remuestreando MESES completos (con reemplazo)."""
    if df.empty or df["month"].nunique() < 3:
        return float("nan"), float("nan")
    g = df.groupby("month")[col].agg(["sum", "count"])
    s, c = g["sum"].to_numpy(), g["count"].to_numpy()
    idx = np.random.default_rng(seed).integers(0, len(g), (n, len(g)))
    means = s[idx].sum(axis=1) / c[idx].sum(axis=1)
    tail = (100.0 - level) / 2
    return float(np.percentile(means, tail)), float(np.percentile(means, 100 - tail))


def summarize(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"n": 0, "win": np.nan, "avg_win": np.nan, "avg_loss": np.nan, "e": np.nan, "e_r": np.nan,
                "pf": np.nan, "total": 0.0, "lo": np.nan, "hi": np.nan, "q_pos": 0, "q_n": 0}
    w, l = df.loc[df["pnl"] > 0, "pnl"], df.loc[df["pnl"] <= 0, "pnl"]
    q = df.groupby("quarter")["pnl"].agg(["sum", "count"])
    q = q[q["count"] >= MIN_Q_TRADES]
    lo, hi = boot_ci(df)
    return {"n": len(df), "win": len(w) / len(df), "avg_win": w.mean() if len(w) else np.nan,
            "avg_loss": l.mean() if len(l) else np.nan, "e": df["pnl"].mean(), "e_r": df["r"].mean(),
            "pf": w.sum() / abs(l.sum()) if l.sum() < 0 else np.inf, "total": df["pnl"].sum(),
            "lo": lo, "hi": hi, "q_pos": int((q["sum"] > 0).sum()), "q_n": len(q)}


def verdict(s: dict) -> str:
    ok = (s["n"] >= MIN_TRADES and s["e"] > 0 and s["lo"] > 0 and s["q_n"] > 0 and s["q_pos"] / s["q_n"] >= Q_SHARE)
    return "PASA" if ok else "no pasa"


def cell_table(trades: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (strat, reg), g in trades.groupby(["strategy", "regime"]):
        rows.append({"strategy": strat, "regime": reg, **summarize(g)})
    t = pd.DataFrame(rows)
    if len(t):
        t["verdict"] = [verdict(r) for r in t.to_dict("records")]
    return t


def walk_forward(trades: pd.DataFrame, window: int, min_n: int = 20) -> dict:
    """Para cada mes: en cada regimen elige la estrategia con mejor resultado medio positivo en los `window` meses
    anteriores (si ninguna, se queda fuera) y la opera SOLO ese mes. Compara con operar todas y con el control al azar."""
    months = sorted(trades["month"].unique())
    cand = [s for s in trades["strategy"].unique() if s != "control"]
    sel, pool, ctl, decisions = [], [], [], []
    for k in range(window, len(months)):
        cur, past = months[k], months[k - window:k]
        pw, cm = trades[trades["month"].isin(past)], trades[trades["month"] == cur]
        for reg in ("up", "down", "range"):
            best, best_e = None, 0.0
            for s in cand:
                x = pw[(pw["strategy"] == s) & (pw["regime"] == reg)]
                if len(x) >= min_n and x["pnl"].mean() > best_e:
                    best, best_e = s, x["pnl"].mean()
            nxt = cm[cm["regime"] == reg]
            pool.append(nxt[nxt["strategy"].isin(cand)])
            ctl.append(nxt[nxt["strategy"] == "control"])
            if best:
                sel.append(nxt[nxt["strategy"] == best])
            decisions.append({"month": cur, "regime": reg, "strategy": best or "fuera"})
    cat = lambda xs: pd.concat(xs, ignore_index=True) if xs else trades.iloc[0:0]  # noqa: E731
    return {"seleccion": summarize(cat(sel)), "todas": summarize(cat(pool)), "control": summarize(cat(ctl)),
            "decisions": pd.DataFrame(decisions), "months": max(len(months) - window, 0)}
