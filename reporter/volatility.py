"""Pronostico del rango de las proximas 24 h (volatilidad, NO direccion).

Modelo validado en research/ (walk-forward, 40 meses, 4 monedas): regresion lineal en logs con el rango de las ultimas 24 h,
la media de ese rango en 7 dias y en 30 dias, calculados con velas de 1h cerradas. Los coeficientes estan en vol_model.json
(se regeneran con research/fit_vol_model.py). Si falta el modelo o los datos, no hay pronostico y el reporter sigue igual.

rango R = ln(maximo / minimo) de 24 h; porcentaje equivalente = e^R - 1.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

MODEL_PATH = Path(__file__).parent / "vol_model.json"
NEEDED = 720 + 24  # 30 dias de velas de 1h mas la ventana de 24 h
HIGH, LOW = 1.25, 0.8  # pronostico / media de 30 dias para etiquetar alta o baja
STOP_FRACTION = 0.3  # heuristica: el stop sugerido es ~30 % del rango esperado de 24 h (no validada)


def load_model(path=MODEL_PATH) -> dict | None:
    try:
        m = json.loads(Path(path).read_text(encoding="utf-8"))
        return m if isinstance(m.get("coins"), dict) else None
    except (OSError, ValueError, AttributeError):
        return None


def _pct(r: float) -> float:
    return (math.exp(r) - 1) * 100


def features(df1h: pd.DataFrame) -> dict | None:
    """Variables en la ultima vela 1h cerrada. None si no hay historial suficiente o los datos no son validos."""
    if df1h is None or len(df1h) < NEEDED:
        return None
    r1 = np.log(df1h["high"].rolling(24).max() / df1h["low"].rolling(24).min())
    last = (r1.iloc[-1], r1.rolling(168).mean().iloc[-1], r1.rolling(720).mean().iloc[-1])
    if not all(np.isfinite(x) and x > 0 for x in last):
        return None
    return {"as_of": (df1h["open_time"].iloc[-1] + pd.Timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "r1": float(last[0]), "r7": float(last[1]), "r30": float(last[2])}


def forecast(df1h: pd.DataFrame, model: dict | None, symbol: str) -> dict | None:
    """Rango esperado de las proximas 24 h (a partir de `as_of`) con una banda de incertidumbre. Nunca lanza."""
    try:
        f, c = features(df1h), (model or {}).get("coins", {}).get(symbol)
        if f is None or c is None:
            return None
        b = c["coef"]
        r = math.exp(b[0] + b[1] * math.log(f["r1"]) + b[2] * math.log(f["r7"]) + b[3] * math.log(f["r30"]))
        fac = math.exp(float(c["rmse_log"]))
        ratio = r / f["r30"]
        return {"as_of": f["as_of"], "r": r, "r_lo": r / fac, "r_hi": r * fac, "r1": f["r1"], "r30": f["r30"],
                "range_pct": _pct(r), "lo_pct": _pct(r / fac), "hi_pct": _pct(r * fac), "r1_pct": _pct(f["r1"]),
                "level": "alto" if ratio > HIGH else "bajo" if ratio < LOW else "normal",
                "stop_pct": STOP_FRACTION * _pct(r)}
    except (KeyError, IndexError, TypeError, ValueError, OverflowError):
        return None
