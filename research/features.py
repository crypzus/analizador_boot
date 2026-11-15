"""Indicadores y regimen de mercado calculados SOLO con datos ya cerrados (sin mirar el futuro).

Una vela de 1h o 4h solo se usa desde el instante en que cierra (`avail`). Las ventanas moviles son hacia atras.
Regimen (velas 4h): distancia EMA20-EMA50 medida en ATR; > +BAND alcista, < -BAND bajista, en medio lateral.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

BAND = 0.5  # umbral del regimen, en ATR de 4h
M15 = pd.Timedelta(minutes=15)


def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def _atr(h: pd.Series, l: pd.Series, c: pd.Series, n: int = 14) -> pd.Series:
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def resample(d: pd.DataFrame, rule: str, bars: int) -> pd.DataFrame:
    """Velas completas de `rule` (con todas sus `bars` velas de 15m). `avail` = instante en que cierran."""
    g = d.set_index("open_time").resample(rule, label="left", closed="left")
    out = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
                volume=("volume", "sum"), n=("close", "count"))
    out = out[out["n"] == bars].drop(columns="n").reset_index()
    out["avail"] = out["open_time"] + pd.Timedelta(rule)
    return out


def build(df: pd.DataFrame, band: float = BAND) -> pd.DataFrame:
    d = df[["open_time", "open", "high", "low", "close", "volume"]].reset_index(drop=True).copy()
    d["end"] = d["open_time"] + M15

    h1 = resample(d, "1h", 4)
    h1["ema1h"] = _ema(h1["close"], 20)
    h1["atr1h"] = _atr(h1["high"], h1["low"], h1["close"])
    h1.loc[:29, ["ema1h", "atr1h"]] = np.nan  # calentamiento de los indicadores

    h4 = resample(d, "4h", 16)
    e20, e50, atr4 = _ema(h4["close"], 20), _ema(h4["close"], 50), _atr(h4["high"], h4["low"], h4["close"])
    dist = (e20 - e50) / atr4
    dist.iloc[:60] = np.nan
    h4["regime"] = np.select([dist > band, dist < -band, dist.notna()], ["up", "down", "range"], default=None)
    h4["regime"] = h4["regime"].where(dist.notna(), None)

    d = pd.merge_asof(d, h1[["avail", "ema1h", "atr1h"]], left_on="end", right_on="avail", direction="backward")
    d = d.drop(columns="avail")
    d = pd.merge_asof(d, h4[["avail", "regime"]], left_on="end", right_on="avail", direction="backward")
    d = d.drop(columns="avail")

    d["rel_vol"] = d["volume"] / d["volume"].rolling(96).mean().shift(1)
    d["hh16"], d["ll16"] = d["high"].rolling(16).max().shift(1), d["low"].rolling(16).min().shift(1)
    d["box_hi"], d["box_lo"] = d["high"].rolling(96).max().shift(1), d["low"].rolling(96).min().shift(1)
    d["lvl_hi"], d["lvl_lo"] = d["high"].rolling(80).max().shift(16), d["low"].rolling(80).min().shift(16)
    d["ext_up"] = (d["close"] - d["ema1h"]) / d["atr1h"]
    d["ext_dn"] = -d["ext_up"]
    d["pre_up"] = d["ext_up"].rolling(24).max().shift(1)
    d["pre_dn"] = d["ext_dn"].rolling(24).max().shift(1)
    return d
