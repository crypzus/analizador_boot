"""Descarga y validacion de velas publicas de Binance (sin clave, solo lectura)."""
from __future__ import annotations

import time

import pandas as pd
import requests

PAIRS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "ADAUSDT"]
# numero de velas: 4h x 60 dias, 1h x 15 dias, 15m x 24 h
WINDOWS = {"4h": 360, "1h": 360, "15m": 96}
STEP_MS = {"15m": 15 * 60_000, "1h": 3_600_000, "4h": 4 * 3_600_000}
HOSTS = ["https://api.binance.com", "https://data-api.binance.vision"]
COLS = ["open_time", "open", "high", "low", "close", "volume"]


def parse_klines(raw: list, interval: str) -> pd.DataFrame:
    """Convierte la respuesta de Binance en DataFrame y valida orden, huecos y filas completas."""
    if not raw:
        raise ValueError("sin velas")
    if any(len(r) < 6 for r in raw):
        raise ValueError("fila de vela incompleta")
    try:
        rows = [[int(r[0])] + [float(x) for x in r[1:6]] for r in raw]
    except (IndexError, TypeError, ValueError) as e:
        raise ValueError(f"fila de vela incompleta o invalida: {e}") from e
    df = pd.DataFrame(rows, columns=COLS)
    diffs = df["open_time"].diff().dropna()
    if not (diffs == STEP_MS[interval]).all():
        raise ValueError(f"velas {interval} con huecos o desordenadas")
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df


def fetch_klines(symbol: str, interval: str, limit: int) -> pd.DataFrame:
    """Descarga con 3 reintentos; si api.binance.com responde 451 (bloqueo regional) usa data-api.binance.vision."""
    last: Exception | None = None
    for host in HOSTS:
        for attempt in range(3):
            try:
                r = requests.get(f"{host}/api/v3/klines", timeout=15,
                                 params={"symbol": symbol, "interval": interval, "limit": limit})
                if r.status_code == 451:
                    last = RuntimeError("451 bloqueo regional")
                    break  # probar el siguiente host
                r.raise_for_status()
                return parse_klines(r.json(), interval)
            except (requests.RequestException, ValueError) as e:
                last = e
                time.sleep(2 ** attempt)
    raise RuntimeError(f"no se pudieron bajar velas {symbol} {interval}: {last}")


def fetch_all(symbol: str) -> dict[str, pd.DataFrame]:
    return {iv: fetch_klines(symbol, iv, n) for iv, n in WINDOWS.items()}
