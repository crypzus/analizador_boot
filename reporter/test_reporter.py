"""Pruebas sin red del reporter. Ejecutar:  python reporter/test_reporter.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import candles  # noqa: E402

H4 = 4 * 3600 * 1000


def kl(n, step=H4, start=1_700_000_000_000, skip=None, swap=False):
    rows = []
    for i in range(n):
        t = start + i * step
        rows.append([t, "100", "110", "90", str(100 + i), "5", t + step - 1, "0", 0, "0", "0", "0"])
    if skip is not None:
        rows.pop(skip)
    if swap:
        rows[1], rows[2] = rows[2], rows[1]
    return rows


def test_parse_ok():
    df = candles.parse_klines(kl(3), "4h")
    assert len(df) == 3 and df["close"].dtype == float
    assert list(df.columns[:6]) == ["open_time", "open", "high", "low", "close", "volume"]
    assert df["close"].iloc[-1] == 102.0
    print("OK velas: parseo")


def test_parse_gap_raises():
    try:
        candles.parse_klines(kl(5, skip=2), "4h")
    except ValueError:
        print("OK velas: hueco rechazado")
        return
    raise AssertionError("debio lanzar ValueError")


def test_parse_unordered_raises():
    try:
        candles.parse_klines(kl(5, swap=True), "4h")
    except ValueError:
        print("OK velas: desorden rechazado")
        return
    raise AssertionError("debio lanzar ValueError")


def test_parse_incomplete_row_raises():
    bad = kl(3)
    bad[1] = bad[1][:4]
    try:
        candles.parse_klines(bad, "4h")
    except ValueError:
        print("OK velas: fila incompleta rechazada")
        return
    raise AssertionError("debio lanzar ValueError")


import indicators  # noqa: E402
import pandas as pd  # noqa: E402


def mk(closes, highs=None, lows=None):
    n = len(closes)
    return pd.DataFrame({
        "open_time": pd.date_range("2026-01-01", periods=n, freq="4h", tz="UTC"),
        "open": closes,
        "high": highs if highs is not None else [c + 1 for c in closes],
        "low": lows if lows is not None else [c - 1 for c in closes],
        "close": closes, "volume": [5.0] * n})


def test_trend_up_rsi_100():
    a = indicators.analyze(mk([100.0 + i for i in range(60)]))
    assert a["trend"] == "up" and a["rsi14"] == 100.0
    assert a["last_close"] == 159.0 and a["ema20"] < a["last_close"] and a["ema50"] < a["ema20"]
    print("OK indicadores: tendencia alcista, RSI 100")


def test_trend_down():
    a = indicators.analyze(mk([200.0 - i for i in range(60)]))
    assert a["trend"] == "down" and a["rsi14"] == 0.0
    print("OK indicadores: tendencia bajista")


def test_trend_range():
    a = indicators.analyze(mk([100.0] * 60))
    assert a["trend"] == "range" and a["rsi14"] == 50.0
    print("OK indicadores: lateral")


def test_levels_swings():
    closes = [100.0] * 50
    highs = [101.0] * 50
    highs[10], highs[30] = 120.0, 115.0
    lows = [99.0] * 50
    lows[20] = 80.0
    lv = indicators.key_levels(mk(closes, highs, lows))
    assert lv["resistances"] == [115.0, 120.0]  # mas cercana primero
    assert lv["supports"] == [80.0]
    print("OK indicadores: niveles por swings")


def test_short_series_raises():
    try:
        indicators.analyze(mk([100.0] * 49))
    except ValueError:
        print("OK indicadores: serie corta rechazada")
        return
    raise AssertionError("debio lanzar ValueError")


if __name__ == "__main__":
    test_parse_ok()
    test_parse_gap_raises()
    test_parse_unordered_raises()
    test_parse_incomplete_row_raises()
    test_trend_up_rsi_100()
    test_trend_down()
    test_trend_range()
    test_levels_swings()
    test_short_series_raises()
    print("\nTODAS LAS PRUEBAS PASARON")
