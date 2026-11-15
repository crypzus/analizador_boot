"""Historial de velas 15m de Binance (spot, API publica sin clave) con cache local en CSV.

Solo lectura de datos de mercado. La primera vez baja ~3,8 anos por moneda (~130 paginas de 1000 velas);
despues solo anade las velas nuevas.
"""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import requests

DIR = Path(__file__).parent / "data"
HOSTS = ["https://api.binance.com", "https://data-api.binance.vision"]
COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "ADAUSDT"]
START = "2023-01-01"
STEP_MS = 15 * 60_000
COLS = ["open_time", "open", "high", "low", "close", "volume"]


def _fetch_page(symbol: str, start_ms: int) -> list:
    last: Exception | None = None
    for host in HOSTS:
        for attempt in range(3):
            try:
                r = requests.get(f"{host}/api/v3/klines", timeout=20,
                                 params={"symbol": symbol, "interval": "15m", "startTime": start_ms, "limit": 1000})
                if r.status_code == 451:
                    last = RuntimeError("451 bloqueo regional")
                    break
                r.raise_for_status()
                return r.json()
            except requests.RequestException as e:
                last = e
                time.sleep(2 ** attempt)
    raise RuntimeError(f"no se pudieron bajar velas {symbol}: {type(last).__name__}")


def to_frame(rows: list) -> pd.DataFrame:
    return pd.DataFrame([[int(r[0])] + [float(x) for x in r[1:6]] for r in rows], columns=COLS)


def update_cache(symbol: str, fetch=_fetch_page, now_ms: float | None = None, verbose: bool = True) -> Path:
    """Baja las velas cerradas que falten y las guarda en data/<symbol>_15m.csv. Reanuda desde el cache."""
    DIR.mkdir(parents=True, exist_ok=True)
    path = DIR / f"{symbol}_15m.csv"
    old = pd.read_csv(path) if path.exists() else None
    start = int(old["open_time"].iloc[-1]) + STEP_MS if old is not None and len(old) else \
        int(pd.Timestamp(START, tz="UTC").timestamp() * 1000)
    now_ms = time.time() * 1000 if now_ms is None else now_ms
    rows, pages = [], 0
    while start < now_ms:
        page = [r for r in fetch(symbol, start) if len(r) >= 7 and int(r[6]) < now_ms]  # solo velas cerradas
        if not page:
            break
        rows += page
        start = int(page[-1][0]) + STEP_MS
        pages += 1
        if verbose and pages % 25 == 0:
            print(f"  {symbol}: {pages} paginas...", flush=True)
        time.sleep(0.1)
    new = to_frame(rows) if rows else pd.DataFrame(columns=COLS)
    df = pd.concat([x for x in (old, new) if x is not None and len(x)], ignore_index=True)
    df = df.drop_duplicates("open_time").sort_values("open_time")
    df.to_csv(path, index=False)
    return path


def load(symbol: str) -> pd.DataFrame:
    df = pd.read_csv(DIR / f"{symbol}_15m.csv")
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df.reset_index(drop=True)


def coverage(df: pd.DataFrame) -> dict:
    gaps = int((df["open_time"].diff().dropna() != pd.Timedelta(minutes=15)).sum())
    return {"candles": len(df), "from": df["open_time"].iloc[0], "to": df["open_time"].iloc[-1], "gaps": gaps}
