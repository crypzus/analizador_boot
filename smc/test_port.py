"""Pruebas de cordura del port. Ejecutar:  python smc/test_port.py"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from smc_bt import Params, htf_struct, load_ohlcv, pivots, run_backtest  # noqa: E402

DATA = Path(__file__).parent.parent / "user_data" / "data" / "binance" / "futures"


def test_pivots_bruteforce():
    rng = np.random.default_rng(1)
    a = rng.normal(size=500).cumsum()
    L = 5
    got = pivots(a, L, L, True)
    for j in range(2 * L, len(a)):
        c = j - L
        win = a[c - L:c + L + 1]
        expect = a[c] if a[c] == win.max() else np.nan
        assert (np.isnan(got[j]) and np.isnan(expect)) or got[j] == expect, j
    assert np.isnan(got[: 2 * L]).all()
    print("OK pivotes == fuerza bruta")


def test_struct_toy():
    # tendencia alcista con un retroceso y luego ruptura bajista (CHoCH)
    h = np.array([1, 2, 3, 4, 5, 6, 5, 4, 5, 6, 7, 8, 9, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 1, 1, 1, 1, 1], float)
    l = h - 0.5
    c = h - 0.1
    d, ch, age = htf_struct(h, l, c, 2)
    assert d[-1] in (-1, 1)
    print("OK estructura corre en datos de juguete (dir final =", d[-1], ")")


def test_no_lookahead():
    """Si el port no mira al futuro, recortar los datos no cambia las operaciones anteriores al corte."""
    df = load_ohlcv(str(DATA / "SOL_USDT_USDT-15m-futures.feather"))
    df = df[df["date"] >= "2025-01-01"].reset_index(drop=True)
    p = Params(capital=1000.0)
    full = run_backtest(df, p)
    cut = pd.Timestamp("2025-09-01", tz="UTC")
    part = run_backtest(df[df["date"] < cut].reset_index(drop=True), p)
    lim = cut - pd.Timedelta(days=2)
    f = full[full["exit_time"] < lim].reset_index(drop=True)
    q = part[part["exit_time"] < lim].reset_index(drop=True)
    assert len(f) > 0, "sin operaciones para comparar"
    cols = ["side", "entry_time", "exit_time", "entry", "exit"]
    pd.testing.assert_frame_equal(f[cols], q[cols])
    print(f"OK sin lookahead: {len(f)} operaciones identicas con y sin el futuro")


if __name__ == "__main__":
    test_pivots_bruteforce()
    test_struct_toy()
    test_no_lookahead()
