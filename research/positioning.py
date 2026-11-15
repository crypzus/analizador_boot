"""Prueba A: ¿anticipan los extremos de posicionamiento (funding y ratio long/short) el movimiento posterior?

Hipotesis fijadas ANTES de correr (contrarias: el lado saturado paga):
  funding o ratio long/short por ENCIMA del percentil 95 de los 90 dias previos (270 observaciones de 8 h) -> short
  por DEBAJO del percentil 5 -> long
Se mide el rendimiento del precio (spot) desde la apertura de la vela 15m siguiente a cada hora de funding
(00:00, 08:00 y 16:00 UTC) tras 8, 24 y 72 h, con comision de 0,1 % ida y vuelta, sin solapar operaciones por celda.
Horizonte primario: 24 h. Hay 4 celdas primarias (2 variables x 2 extremos): para no inflar falsos positivos
se exige el limite inferior de un IC del 98,75 % (Bonferroni), calculado remuestreando meses completos.
Una celda pasa con >= 100 eventos, rendimiento neto medio > 0, IC inferior > 0 y positivo en >= 2/3 de los trimestres.
"""
from __future__ import annotations

import io
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).parent))
import analysis  # noqa: E402
import data  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FUND = ROOT / "user_data" / "data" / "binance" / "futures"
URL = "https://data.binance.vision/data/futures/um/daily/metrics/{s}/{s}-metrics-{d}.zip"
COST, NOTIONAL = 0.001, 500.0
WINDOW, HI, LO = 270, 95, 5
HORIZONS, PRIMARY_H = (8, 24, 72), 24
BONF_LEVEL = 100 - 5 / 4
FEATURES = {"funding": "funding", "long_short": "ratio long/short"}
NS15 = 15 * 60 * 10 ** 9


def _ns(s: pd.Series) -> np.ndarray:
    return pd.to_datetime(s, utc=True).dt.as_unit("ns").astype("int64").to_numpy()


def load_funding(symbol: str) -> pd.DataFrame:
    df = pd.read_feather(FUND / f"{symbol.replace('USDT', '')}_USDT_USDT-1h-funding_rate.feather")
    out = df.rename(columns={"date": "ts", "funding_rate": "value"})[["ts", "value"]]
    out["ts"] = pd.to_datetime(out["ts"], utc=True)
    return out[out["ts"] >= pd.Timestamp(data.START, tz="UTC")].reset_index(drop=True)


def _get_day(symbol: str, d: date):
    r = requests.get(URL.format(s=symbol, d=d.isoformat()), timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    return pd.read_csv(z.open(z.namelist()[0]), usecols=["create_time", "sum_open_interest", "count_long_short_ratio"])


def _retry(fetch, symbol: str, d: date):
    for attempt in range(3):
        try:
            return fetch(symbol, d)
        except requests.RequestException:
            time.sleep(2 ** attempt)
    return None


def update_metrics(symbol: str, fetch=_get_day, today: date | None = None, workers: int = 8, verbose: bool = True) -> Path:
    """Baja los ficheros diarios de metricas de futuros (data.binance.vision, publicos) que falten. Reanuda desde el cache."""
    data.DIR.mkdir(parents=True, exist_ok=True)
    path = data.DIR / f"{symbol}_metrics.csv"
    old = pd.read_csv(path) if path.exists() else None
    start = (pd.Timestamp(old["create_time"].iloc[-1]).date() + timedelta(days=1)) if old is not None and len(old) \
        else date.fromisoformat(data.START)
    end = (today or date.today()) - timedelta(days=1)
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    frames, missing = [], 0
    with ThreadPoolExecutor(workers) as ex:
        for k, df in enumerate(ex.map(lambda d: _retry(fetch, symbol, d), days), 1):
            if df is None:
                missing += 1
            else:
                frames.append(df)
            if verbose and k % 300 == 0:
                print(f"  {symbol} metricas: {k}/{len(days)} dias", flush=True)
    parts = [x for x in [old] + frames if x is not None and len(x)]
    if parts:
        pd.concat(parts, ignore_index=True).drop_duplicates("create_time").sort_values("create_time").to_csv(path, index=False)
    if verbose and missing:
        print(f"  {symbol}: {missing} dias sin fichero", flush=True)
    return path


def load_metrics(symbol: str) -> pd.DataFrame:
    m = pd.read_csv(data.DIR / f"{symbol}_metrics.csv")
    m["create_time"] = pd.to_datetime(m["create_time"], utc=True)
    return m.sort_values("create_time").reset_index(drop=True)


def at_times(ts: pd.Series, m: pd.DataFrame, col: str) -> np.ndarray:
    """Ultimo valor conocido de `col` en cada instante `ts` (tolerancia 30 min; si no hay dato, NaN)."""
    left = pd.DataFrame({"ts": pd.to_datetime(ts, utc=True).dt.as_unit("ns").reset_index(drop=True)})
    if not left["ts"].is_monotonic_increasing:
        raise ValueError("ts debe estar ordenado")
    right = m[["create_time", col]].dropna().copy()
    right["create_time"] = pd.to_datetime(right["create_time"], utc=True).dt.as_unit("ns")
    j = pd.merge_asof(left, right.sort_values("create_time"), left_on="ts", right_on="create_time",
                      direction="backward", tolerance=pd.Timedelta("30min"))
    return j[col].to_numpy(dtype=float)


def extreme_flags(v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(alto, bajo): valor actual fuera de los percentiles 95/5 de las WINDOW observaciones ANTERIORES."""
    hi, lo = np.zeros(len(v), bool), np.zeros(len(v), bool)
    for k in range(WINDOW, len(v)):
        if np.isnan(v[k]):
            continue
        w = v[k - WINDOW:k]
        w = w[~np.isnan(w)]
        if len(w) < 0.8 * WINDOW:
            continue
        hi[k], lo[k] = v[k] > np.percentile(w, HI), v[k] < np.percentile(w, LO)
    return hi, lo


def _entry_exit(px: pd.DataFrame, ts: pd.Series):
    """Para cada hora de funding: indice de la vela 15m siguiente (-1 si no existe)."""
    pos = {t: i for i, t in enumerate(_ns(px["open_time"]))}
    return np.array([pos.get(t + NS15, -1) for t in _ns(ts)])


def build_events(coin: str, px: pd.DataFrame, ts: pd.Series, feats: dict) -> pd.DataFrame:
    opn, ot, ent = px["open"].to_numpy(), _ns(px["open_time"]), _entry_exit(px, ts)
    rows = []
    for fname, v in feats.items():
        hi, lo = extreme_flags(np.asarray(v, dtype=float))
        for ext, flags, side in (("alto", hi, -1), ("bajo", lo, +1)):
            for h in HORIZONS:
                busy = -1
                for k in np.flatnonzero(flags):
                    i, j = int(ent[k]), int(ent[k]) + 4 * h
                    if i < 0 or i <= busy or j >= len(px) or ot[j] - ot[i] != h * 3600 * 10 ** 9:
                        continue
                    gross = side * (opn[j] / opn[i] - 1)
                    busy = j
                    rows.append({"coin": coin, "feature": fname, "extreme": ext, "horizon": h, "side": side,
                                 "entry_ts": pd.Timestamp(ot[i], tz="UTC"), "gross": gross, "r": gross - COST,
                                 "pnl": NOTIONAL * (gross - COST)})
    df = pd.DataFrame(rows)
    if len(df):
        t = df["entry_ts"].dt.tz_localize(None)
        df["month"], df["quarter"] = t.dt.strftime("%Y-%m"), t.dt.to_period("Q").astype(str)
    return df


def baseline_returns(px: pd.DataFrame, ts: pd.Series) -> dict:
    """Rendimiento medio (long, sin comision) en TODAS las horas de funding, por horizonte: referencia de la deriva."""
    opn, ot, ent = px["open"].to_numpy(), _ns(px["open_time"]), _entry_exit(px, ts)
    out = {}
    for h in HORIZONS:
        r = [opn[int(i) + 4 * h] / opn[int(i)] - 1 for i in ent
             if i >= 0 and int(i) + 4 * h < len(px) and ot[int(i) + 4 * h] - ot[int(i)] == h * 3600 * 10 ** 9]
        out[h] = np.asarray(r)
    return out


def summarize_cells(events: pd.DataFrame, base: dict) -> pd.DataFrame:
    rows = []
    for (feat, ext, h), g in events.groupby(["feature", "extreme", "horizon"]):
        side = int(g["side"].iloc[0])
        lo, hi = analysis.boot_ci(g, level=BONF_LEVEL)
        q = g.groupby("quarter")["pnl"].agg(["sum", "count"])
        q = q[q["count"] >= analysis.MIN_Q_TRADES]
        row = {"feature": feat, "extreme": ext, "horizon": h, "n": len(g), "gross": g["gross"].mean(),
               "net": g["r"].mean(), "base": side * base[h].mean(), "e": g["pnl"].mean(), "lo": lo, "hi": hi,
               "q_pos": int((q["sum"] > 0).sum()), "q_n": len(q)}
        ok = (row["n"] >= analysis.MIN_TRADES and row["e"] > 0 and lo > 0 and row["q_n"] > 0
              and row["q_pos"] / row["q_n"] >= analysis.Q_SHARE)
        row["verdict"] = ("PASA" if ok else "no pasa") if h == PRIMARY_H else "secundario"
        rows.append(row)
    return pd.DataFrame(rows)
