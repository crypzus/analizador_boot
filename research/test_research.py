"""Pruebas de la investigacion por regimen (sin red). Ejecutar: python research/test_research.py"""
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "reporter"))
import analysis  # noqa: E402
import backtest  # noqa: E402
import data  # noqa: E402
import features  # noqa: E402
import setups  # noqa: E402
import strategies  # noqa: E402


def synth(n=6000, seed=1, drift=0.0, vol=0.002, start="2024-01-01"):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    opn = np.r_[100.0, close[:-1]]
    spread = np.abs(rng.normal(0, vol, n)) * close * 0.5
    volume = rng.uniform(1, 3, n) * np.where(rng.random(n) < 0.06, 3.0, 1.0)
    return pd.DataFrame({"open_time": pd.date_range(start, periods=n, freq="15min", tz="UTC"), "open": opn,
                         "high": np.maximum(opn, close) + spread, "low": np.minimum(opn, close) - spread,
                         "close": close, "volume": volume})


def test_features_do_not_use_the_future():
    df = synth(7000, seed=3, drift=0.0003)
    full, m = features.build(df), 5000
    cut = features.build(df.iloc[:m])
    a, b = full.iloc[:m].reset_index(drop=True), cut.iloc[:m].reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b, check_dtype=False)
    print("OK features: recortar el futuro no cambia ningun indicador ni regimen pasado")


def test_signals_do_not_use_the_future():
    df = synth(9000, seed=5, drift=0.0004)
    full_d, m = features.build(df), 6500
    cut_d = features.build(df.iloc[:m])
    key = lambda s: (s["strategy"], s["i"], s["side"], round(s["entry"], 9), round(s["stop"], 9), s["created_ts"])  # noqa: E731
    full = sorted(key(s) for s in strategies.generate(full_d, "X") if s["i"] <= m - 2)
    cut = sorted(key(s) for s in strategies.generate(cut_d, "X"))
    assert full == cut and len(full) > 20
    assert {s[0] for s in full} >= {"control", "impulso"}
    for s in strategies.generate(full_d, "X"):
        setups.parse_setup(s)  # todas las senales son coherentes
    print(f"OK estrategias: las {len(full)} senales anteriores al corte son identicas con y sin futuro")


def test_regime_detection():
    up = features.build(synth(4000, seed=2, drift=0.001, vol=0.001))["regime"].dropna()
    assert (up == "up").mean() > 0.8
    rng = np.random.default_rng(4)
    c = 100 + rng.normal(0, 0.3, 4000)
    flat = pd.DataFrame({"open_time": pd.date_range("2024-01-01", periods=4000, freq="15min", tz="UTC"),
                         "open": np.r_[100.0, c[:-1]], "high": np.maximum(np.r_[100.0, c[:-1]], c) + 0.2,
                         "low": np.minimum(np.r_[100.0, c[:-1]], c) - 0.2, "close": c, "volume": 1.0})
    assert (features.build(flat)["regime"].dropna() == "range").mean() > 0.8
    print("OK regimen: tendencia clara es alcista y ruido sin direccion es lateral")


def test_simulate_hold_hours():
    s = {"side": "short", "entry": 100.0, "stop": 104.0, "tp1": 92.0, "tp2": 88.0, "created_ts": "2026-01-01T00:00:00Z"}
    t0 = pd.Timestamp("2026-01-01", tz="UTC")
    rows = [(98, 100.5, 97, 99)] + [(97, 99, 95, 96)] * 10
    df = pd.DataFrame([{"open_time": t0 + pd.Timedelta(minutes=15 * i), "open": o, "high": h, "low": l, "close": c}
                       for i, (o, h, l, c) in enumerate(rows)])
    r = setups.simulate(s, df, hold_hours=1)
    assert r["outcome"] == "timeout" and r["pnl"] == 19.5  # cerro tras 1 h al ultimo cierre (96)
    print("OK simulador: el plazo de cierre es configurable")


def test_backtest_run():
    t0 = pd.Timestamp("2026-01-01", tz="UTC")
    rows = [(100, 100, 100, 100),                       # vela de la senal
            (98, 100.5, 97, 99), (99, 100, 91, 92), (92, 93, 87, 88)] + [(88, 88, 88, 88)] * 20
    d = pd.DataFrame([{"open_time": t0 + pd.Timedelta(minutes=15 * i), "open": o, "high": h, "low": l, "close": c}
                      for i, (o, h, l, c) in enumerate(rows)])
    base = {"coin": "X", "strategy": "s", "regime": "down", "side": "short", "entry": 100.0, "stop": 104.0, "tp1": 92.0,
            "tp2": 88.0, "stop_dist": 4.0, "created_ts": "2026-01-01T00:15:00Z", "entry_hours": 4.0}
    nofill = {**base, "i": 10, "entry": 120.0, "stop": 124.0, "tp1": 112.0, "tp2": 108.0, "created_ts": "2026-01-01T02:45:00Z"}
    tr, c = backtest.run(d, [{**base, "i": 0}, nofill], hold_hours=12)
    assert len(tr) == 1 and tr.iloc[0]["outcome"] == "tp2" and tr.iloc[0]["pnl"] == 49.5
    assert round(tr.iloc[0]["r"], 3) == round(49.5 / 20, 3) and c["no_fill"] == 0 and c["unfinished"] == 1
    print("OK backtest: cierra operaciones, calcula R y separa las no terminadas")


def _trades(spec):
    rows = []
    for month, strat, reg, pnl, n in spec:
        for _ in range(n):
            rows.append({"strategy": strat, "regime": reg, "pnl": pnl, "r": pnl / 20, "month": month,
                         "quarter": pd.Period(month, "M").asfreq("Q").strftime("%YQ%q"), "coin": "X"})
    return pd.DataFrame(rows)


def test_analysis_verdict_and_walk_forward():
    months = [f"2024-{m:02d}" for m in range(1, 9)]
    good = _trades([(m, "A", "up", 5.0 if i % 4 else -2.0, 30) for i, m in enumerate(months)])
    bad = _trades([(m, "B", "up", -3.0, 30) for m in months])
    ctl = _trades([(m, "control", "up", -1.0, 30) for m in months])
    t = pd.concat([good, bad, ctl], ignore_index=True)
    cells = analysis.cell_table(t).set_index(["strategy", "regime"])
    assert cells.loc[("A", "up"), "verdict"] == "PASA" and cells.loc[("B", "up"), "verdict"] == "no pasa"
    lo, hi = analysis.boot_ci(good)
    assert lo < good["pnl"].mean() < hi
    wf = analysis.walk_forward(t, window=2)
    assert wf["seleccion"]["n"] == 6 * 30 and wf["seleccion"]["e"] > 0 and wf["control"]["e"] < 0
    assert set(wf["decisions"].query("regime == 'up'")["strategy"]) == {"A"}
    none = analysis.walk_forward(pd.concat([bad, ctl], ignore_index=True), window=2)
    assert none["seleccion"]["n"] == 0
    print("OK analisis: veredicto, IC por meses y seleccion por ventanas (solo opera lo que venia ganando)")


def test_update_cache_resumes_and_keeps_closed_only():
    step = data.STEP_MS
    t0 = int(pd.Timestamp(data.START, tz="UTC").timestamp() * 1000)
    allrows = [[t0 + i * step, 1, 2, 0.5, 1.5, 10, t0 + (i + 1) * step - 1] for i in range(2500)]

    def fetch(symbol, start_ms):
        return [r for r in allrows if r[0] >= start_ms][:1000]

    old_dir = data.DIR
    data.DIR = Path(tempfile.mkdtemp())
    try:
        data.update_cache("TESTUSDT", fetch=fetch, now_ms=t0 + 2000 * step + 5, verbose=False)
        assert len(data.load("TESTUSDT")) == 2000  # la vela 2000 aun no cerro
        data.update_cache("TESTUSDT", fetch=fetch, now_ms=t0 + 2500 * step + 5, verbose=False)
        df = data.load("TESTUSDT")
        assert len(df) == 2500 and df["open_time"].is_unique and data.coverage(df)["gaps"] == 0
    finally:
        data.DIR = old_dir
    print("OK datos: la cache reanuda, no duplica y solo guarda velas cerradas")


if __name__ == "__main__":
    test_update_cache_resumes_and_keeps_closed_only()
    test_simulate_hold_hours()
    test_backtest_run()
    test_regime_detection()
    test_features_do_not_use_the_future()
    test_signals_do_not_use_the_future()
    test_analysis_verdict_and_walk_forward()
    print("\nTODAS LAS PRUEBAS DE INVESTIGACION PASARON")
