"""Pruebas de la ronda 2 (posicionamiento y volatilidad), sin red. Ejecutar: python research/test_round2.py"""
import sys
import tempfile
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "reporter"))
import analysis  # noqa: E402
import data  # noqa: E402
import positioning as pos  # noqa: E402
import volatility as vol  # noqa: E402
from test_research import synth  # noqa: E402


def test_extreme_flags_use_only_the_past():
    rng = np.random.default_rng(0)
    v = rng.normal(0, 1, 600)
    v[400], v[450] = 9.0, -9.0
    hi, lo = pos.extreme_flags(v)
    assert hi[400] and lo[450] and hi[:pos.WINDOW].sum() == 0
    hi2, lo2 = pos.extreme_flags(v[:420])  # recortar el futuro no cambia nada antes del corte
    assert (hi[:420] == hi2).all() and (lo[:420] == lo2).all()
    print("OK posicionamiento: los extremos usan solo las observaciones anteriores")


def test_build_events_returns_and_no_overlap():
    n = 4 * 24 * 140
    ot = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    px = pd.DataFrame({"open_time": ot, "open": 100.0 * (1.0001 ** np.arange(n))})  # sube 0,01 % por vela
    ts = pd.Series(pd.date_range("2024-01-01", periods=400, freq="8h", tz="UTC"))
    v = np.full(400, 1.0)
    v[300], v[301] = 5.0, 5.0  # dos extremos seguidos: el segundo empieza donde acaba el primero y se ignora
    ev = pos.build_events("X", px, ts, {"funding": v})
    alto = ev[ev["extreme"] == "alto"]
    assert len(alto[alto["horizon"] == 8]) == 1 and len(alto[alto["horizon"] == 24]) == 1
    assert len(ev[ev["extreme"] == "bajo"]) == 0
    r = alto[alto["horizon"] == 24].iloc[0]
    expect = -((1.0001 ** (4 * 24)) - 1)  # short con el precio subiendo: pierde
    assert abs(r["gross"] - expect) < 1e-12 and abs(r["r"] - (expect - pos.COST)) < 1e-12
    assert abs(r["pnl"] - pos.NOTIONAL * r["r"]) < 1e-9
    # la entrada es la vela 15m SIGUIENTE a la hora de funding
    assert r["entry_ts"] == ts.iloc[300] + pd.Timedelta(minutes=15)
    print("OK posicionamiento: rendimiento, comision, entrada en la vela siguiente y sin solapar")


def test_update_metrics_resumes_and_skips_missing():
    old_dir = data.DIR
    data.DIR = Path(tempfile.mkdtemp())
    calls = []

    def fetch(symbol, d):
        calls.append(d)
        if d == date(2023, 1, 3):
            return None  # dia sin fichero
        return pd.DataFrame({"create_time": [f"{d} 00:00:00", f"{d} 00:05:00"], "sum_open_interest": [1.0, 2.0],
                             "count_long_short_ratio": [1.1, 1.2]})
    try:
        pos.update_metrics("TESTUSDT", fetch=fetch, today=date(2023, 1, 6), verbose=False)
        m = pos.load_metrics("TESTUSDT")
        assert len(m) == 8 and m["create_time"].is_unique  # 2023-01-01..05 menos el dia 3 = 4 dias x 2
        calls.clear()
        pos.update_metrics("TESTUSDT", fetch=fetch, today=date(2023, 1, 8), verbose=False)
        assert sorted(calls) == [date(2023, 1, 6), date(2023, 1, 7)]  # reanuda desde el ultimo dia guardado
        assert len(pos.load_metrics("TESTUSDT")) == 12
    finally:
        data.DIR = old_dir
    print("OK metricas: la cache reanuda, tolera dias sin fichero y no duplica")


def test_at_times_uses_last_known_value():
    m = pd.DataFrame({"create_time": pd.to_datetime(["2024-01-01 00:00", "2024-01-01 00:05", "2024-01-01 08:00"], utc=True),
                      "r": [1.0, 2.0, 3.0]})
    ts = pd.Series(pd.to_datetime(["2024-01-01 00:07", "2024-01-01 08:00", "2024-01-01 16:00"], utc=True))
    out = pos.at_times(ts, m, "r")
    assert out[0] == 2.0 and out[1] == 3.0 and np.isnan(out[2])  # el ultimo se queda sin dato (mas de 30 min)
    print("OK metricas: ultimo valor conocido con tolerancia")


def test_boot_ci_level():
    g = pd.DataFrame({"month": [f"2024-{m:02d}" for m in range(1, 11) for _ in range(5)],
                      "pnl": np.random.default_rng(2).normal(1, 3, 50)})
    lo95, hi95 = analysis.boot_ci(g)
    lo99, hi99 = analysis.boot_ci(g, level=99)
    assert lo99 <= lo95 < hi95 <= hi99
    print("OK analisis: el IC se ensancha al subir el nivel")


def _vol_prices(n=60000, seed=7):
    rng = np.random.default_rng(seed)
    regime = np.repeat(rng.choice([0.0005, 0.0015, 0.004], size=n // 600 + 1, p=[0.4, 0.4, 0.2]), 600)[:n]
    df = synth(n, seed=seed, vol=0.001)
    close = 100 * np.exp(np.cumsum(rng.normal(0, regime)))
    opn = np.r_[100.0, close[:-1]]
    sp = np.abs(rng.normal(0, regime)) * close * 0.5
    return pd.DataFrame({"open_time": df["open_time"], "open": opn, "high": np.maximum(opn, close) + sp,
                         "low": np.minimum(opn, close) - sp, "close": close, "volume": 1.0})


def test_volatility_no_leak_and_har_beats_persistence():
    px = _vol_prices()
    s = vol.samples(px)
    cut = vol.samples(px.iloc[:40000])
    a = s[s["end"] <= cut["end"].iloc[-1]].reset_index(drop=True)
    b = cut[cut["end"] <= a["end"].iloc[-1]].reset_index(drop=True)
    cols = ["end", "lr1", "lr7", "lr30"]
    pd.testing.assert_frame_equal(a[cols].iloc[:len(b)], b[cols], check_dtype=False)  # variables sin futuro
    m = sorted(s["month"].unique())[8]
    tm = vol.train_mask(s, m)
    assert tm.sum() > 0 and s.loc[tm, "target_end"].max() <= pd.Timestamp(m + "-01", tz="UTC")
    res = vol.walk_forward(s, "X")
    assert len(res) >= 3 and (res["mse_har"].mean() < res["mse_pers"].mean())
    print("OK volatilidad: variables sin futuro, entrenamiento solo con objetivos ya terminados y el modelo mejora a la persistencia")


def test_volatility_summary_verdict():
    rows = [{"coin": c, "month": f"2024-{m:02d}", "n": 100, "mse_har": 0.8, "mse_pers": 1.0, "mse_m30": 1.1}
            for c in ("A", "B") for m in range(1, 13)]
    ok = vol.summarize(pd.DataFrame(rows), n_boot=200)
    assert ok["verdict"] == "PASA" and abs(ok["reduction"] - 0.2) < 1e-9
    bad = [{**r, "mse_har": 1.2} for r in rows]
    assert vol.summarize(pd.DataFrame(bad), n_boot=200)["verdict"] == "no pasa"
    print("OK volatilidad: el veredicto exige mejora real frente a las referencias")


if __name__ == "__main__":
    test_extreme_flags_use_only_the_past()
    test_build_events_returns_and_no_overlap()
    test_update_metrics_resumes_and_skips_missing()
    test_at_times_uses_last_known_value()
    test_boot_ci_level()
    test_volatility_summary_verdict()
    test_volatility_no_leak_and_har_beats_persistence()
    print("\nTODAS LAS PRUEBAS DE LA RONDA 2 PASARON")
