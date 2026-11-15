"""Prueba B: ¿se puede predecir cuanto se movera el precio en las proximas 24 h (no la direccion)?

Objetivo: log del rango de las 24 h siguientes, log(max alto / min bajo), muestreado cada 4 h.
Modelo (tipo HAR, regresion lineal en logs) con tres variables conocidas en ese instante: rango de las ultimas 24 h,
rango medio de los ultimos 7 dias y de los ultimos 30 dias. Se entrena con una ventana creciente que solo incluye
muestras cuyo objetivo ya termino antes del mes evaluado (walk-forward, sin fuga de datos).
Referencias: persistencia (el rango de las ultimas 24 h) y media de 30 dias.
Criterio fijado antes de correr: el modelo pasa si reduce el error cuadratico medio >= 5 % frente a la MEJOR referencia,
el limite inferior del IC 95 % (remuestreo por meses) de esa reduccion es > 0 y gana a ambas referencias en >= 2/3 de
los meses-moneda evaluados.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MIN_TRAIN_MONTHS, MIN_REDUCTION, WIN_SHARE = 6, 0.05, 2 / 3
M15 = pd.Timedelta(minutes=15)
FEATS = ["lr1", "lr7", "lr30"]


def samples(df: pd.DataFrame) -> pd.DataFrame:
    """Una fila cada 4 h (instantes con minuto 0 y hora multiplo de 4). Las variables solo usan velas ya cerradas."""
    d = df.reset_index(drop=True)
    r1 = np.log(d["high"].rolling(96).max() / d["low"].rolling(96).min())
    end = d["open_time"] + M15
    s = pd.DataFrame({"end": end, "r1": r1, "r7": r1.rolling(672).mean(), "r30": r1.rolling(2880).mean(),
                      "y": r1.shift(-96), "target_end": end.shift(-96)})
    s = s[(end.dt.minute == 0) & (end.dt.hour % 4 == 0)].dropna()
    s = s[(s[["r1", "r7", "r30", "y"]] > 0).all(axis=1)].copy()
    for c in ("r1", "r7", "r30"):
        s["l" + c] = np.log(s[c])
    s["ly"] = np.log(s["y"])
    s["month"] = s["end"].dt.tz_localize(None).dt.strftime("%Y-%m")
    return s.reset_index(drop=True)


def train_mask(s: pd.DataFrame, month: str) -> np.ndarray:
    """Muestras de entrenamiento permitidas para predecir `month`: su objetivo termino antes de que empiece el mes."""
    return (s["target_end"] <= pd.Timestamp(month + "-01", tz="UTC")).to_numpy()


def _fit(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    return np.linalg.lstsq(np.c_[np.ones(len(x)), x], y, rcond=None)[0]


def walk_forward(s: pd.DataFrame, coin: str) -> pd.DataFrame:
    months = sorted(s["month"].unique())
    rows = []
    for m in months[MIN_TRAIN_MONTHS:]:
        tr, te = s[train_mask(s, m)], s[s["month"] == m]
        if len(tr) < 200 or te.empty:
            continue
        b = _fit(tr[FEATS].to_numpy(), tr["ly"].to_numpy())
        pred = np.c_[np.ones(len(te)), te[FEATS].to_numpy()] @ b
        y = te["ly"].to_numpy()
        rows.append({"coin": coin, "month": m, "n": len(te), "mse_har": float(np.mean((pred - y) ** 2)),
                     "mse_pers": float(np.mean((te["lr1"].to_numpy() - y) ** 2)),
                     "mse_m30": float(np.mean((te["lr30"].to_numpy() - y) ** 2))})
    return pd.DataFrame(rows)


def _reduction(g: pd.DataFrame) -> tuple[float, float]:
    n = g["n"].to_numpy()
    har = (g["mse_har"] * n).sum() / n.sum()
    best = min((g["mse_pers"] * n).sum(), (g["mse_m30"] * n).sum()) / n.sum()
    return 1 - har / best, har


def summarize(res: pd.DataFrame, n_boot: int = 2000, seed: int = 1) -> dict:
    """Resumen conjunto de todas las monedas. IC remuestreando meses completos (todas las monedas de ese mes juntas)."""
    n = res["n"].to_numpy()
    tot = {k: (res[k] * n).sum() / n.sum() for k in ("mse_har", "mse_pers", "mse_m30")}
    best_key = "mse_pers" if tot["mse_pers"] <= tot["mse_m30"] else "mse_m30"
    months = sorted(res["month"].unique())
    by_m = {m: res[res["month"] == m] for m in months}
    rng = np.random.default_rng(seed)
    red = []
    for _ in range(n_boot):
        g = pd.concat([by_m[months[i]] for i in rng.integers(0, len(months), len(months))])
        red.append(_reduction(g)[0])
    wins = ((res["mse_har"] < res["mse_pers"]) & (res["mse_har"] < res["mse_m30"])).mean()
    out = {"mse": tot, "best": best_key, "reduction": 1 - tot["mse_har"] / tot[best_key],
           "lo": float(np.percentile(red, 2.5)), "hi": float(np.percentile(red, 97.5)), "win_share": float(wins),
           "n_months": len(months), "n": int(n.sum()),
           "rmse_factor": {k: float(np.exp(np.sqrt(v))) for k, v in tot.items()}}
    out["verdict"] = "PASA" if (out["reduction"] >= MIN_REDUCTION and out["lo"] > 0 and wins >= WIN_SHARE) else "no pasa"
    return out
