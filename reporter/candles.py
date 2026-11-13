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


def drop_unclosed(raw: list, now_ms: float | None = None) -> list:
    """Quita la vela en formacion (su cierre esta en el futuro): su volumen parcial distorsiona indicadores."""
    now_ms = time.time() * 1000 if now_ms is None else now_ms
    return [r for r in raw if len(r) < 7 or int(r[6]) < now_ms]


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
                return parse_klines(drop_unclosed(r.json()), interval)
            except (requests.RequestException, ValueError) as e:
                last = e
                time.sleep(2 ** attempt)
    raise RuntimeError(f"no se pudieron bajar velas {symbol} {interval}: {last}")


def candle_start_ms(ts_iso: str) -> int:
    """Apertura (ms) de la ultima vela 15m ya cerrada en ts+24 h. Las velas estan alineadas a :00/:15/:30/:45."""
    from datetime import datetime, timedelta, timezone
    target = datetime.strptime(ts_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) + timedelta(hours=24)
    step = STEP_MS["15m"]
    return int(target.timestamp() * 1000) // step * step - step


def fetch_price_at(symbol: str, ts_iso: str) -> float | None:
    """Cierre de la vela 15m que termina en ts+24 h. None si aun no existe o la red falla (queda pendiente)."""
    start = candle_start_ms(ts_iso)
    for host in HOSTS:
        try:
            r = requests.get(f"{host}/api/v3/klines", timeout=15,
                             params={"symbol": symbol, "interval": "15m", "startTime": start, "limit": 1})
            if r.status_code == 451:
                continue
            r.raise_for_status()
            rows = r.json()
            if rows and int(rows[0][0]) == start and int(rows[0][6]) < time.time() * 1000:
                return float(rows[0][4])
            return None
        except (requests.RequestException, ValueError, IndexError):
            continue
    return None


def fetch_all(symbol: str) -> dict[str, pd.DataFrame]:
    return {iv: fetch_klines(symbol, iv, n) for iv, n in WINDOWS.items()}
