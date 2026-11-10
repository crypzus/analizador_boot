"""Indicadores y niveles clave. Funciones puras: velas entran, numeros salen."""
from __future__ import annotations

import pandas as pd

MIN_CANDLES = 50


def _wilder(s: pd.Series, n: int = 14) -> pd.Series:
    return s.ewm(alpha=1 / n, adjust=False).mean()


def analyze(df: pd.DataFrame) -> dict:
    if len(df) < MIN_CANDLES:
        raise ValueError(f"se necesitan al menos {MIN_CANDLES} velas (hay {len(df)})")
    close = df["close"]
    ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
    ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]

    delta = close.diff()
    avg_gain = _wilder(delta.clip(lower=0)).iloc[-1]
    avg_loss = _wilder((-delta).clip(lower=0)).iloc[-1]
    if avg_gain == 0 and avg_loss == 0:
        rsi = 50.0  # precio plano
    elif avg_loss == 0:
        rsi = 100.0
    else:
        rsi = 100 - 100 / (1 + avg_gain / avg_loss)

    prev = close.shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
    atr = _wilder(tr).iloc[-1]

    last = close.iloc[-1]
    base_vol = df["volume"].iloc[-21:-1].mean()
    trend = "up" if last > ema20 > ema50 else "down" if last < ema20 < ema50 else "range"
    return {"last_close": float(last), "ema20": float(ema20), "ema50": float(ema50), "rsi14": float(rsi),
            "atr14": float(atr), "rel_volume": float(df["volume"].iloc[-1] / base_vol) if base_vol else 0.0,
            "high": float(df["high"].max()), "low": float(df["low"].min()), "trend": trend}


def key_levels(df: pd.DataFrame, n: int = 3, wing: int = 3) -> dict:
    """Soportes/resistencias por swings: maximo (minimo) estricto frente a `wing` velas a cada lado."""
    hi, lo, last = df["high"].tolist(), df["low"].tolist(), float(df["close"].iloc[-1])
    res, sup = [], []
    for i in range(wing, len(df) - wing):
        around_h = hi[i - wing:i] + hi[i + 1:i + 1 + wing]
        around_l = lo[i - wing:i] + lo[i + 1:i + 1 + wing]
        if hi[i] > max(around_h) and hi[i] > last:
            res.append(float(hi[i]))
        if lo[i] < min(around_l) and lo[i] < last:
            sup.append(float(lo[i]))
    return {"resistances": sorted(set(res), key=lambda x: x - last)[:n],
            "supports": sorted(set(sup), key=lambda x: last - x)[:n]}
