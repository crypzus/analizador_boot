"""Pruebas del motor ny_breakout (sin red). Ejecutar: python research/test_ny.py"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import ny_breakout as ny  # noqa: E402


def synth(n=24000, seed=1, drift=0.0003, vol=0.002, start="2024-01-01"):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    opn = np.r_[100.0, close[:-1]]
    spread = np.abs(rng.normal(0, vol, n)) * close * 0.5
    volume = rng.uniform(1, 3, n) * np.where(rng.random(n) < 0.08, 3.0, 1.0)
    return pd.DataFrame({"open_time": pd.date_range(start, periods=n, freq="15min", tz="UTC"), "open": opn,
                         "high": np.maximum(opn, close) + spread, "low": np.minimum(opn, close) - spread,
                         "close": close, "volume": volume})


def hand(rows, ema=None, start="2024-03-01 13:00"):
    """Velas a mano: lista de (open, high, low, close). ATR/EMA se pasan aparte."""
    n = len(rows)
    ts = pd.Series(pd.date_range(start, periods=n, freq="15min", tz="UTC"))
    o, h, l, c = (np.array([r[i] for r in rows], float) for i in range(4))
    return {"n": n, "o": o, "h": h, "l": l, "c": c, "ema": np.full(n, 50.0) if ema is None else np.array(ema, float),
            "ts": ts, "ts_ns": ny.ts_to_ns(ts)}


P0 = ny.params(funding=0.0, cost_mult=0.0)       # sin costos para comprobar las reglas de salida


def test_stop_wins_when_stop_and_target_touch_same_candle():
    # entrada 100, sd = 1.2 * (1/1.2) = 1 -> stop 99, TP1 101
    a = hand([(100, 101.5, 98.5, 100), (100, 100, 100, 100)])
    t = ny.simulate_trade(a, 0, "long", 1 / 1.2, P0)
    assert t["reason"] == "stop" and abs(t["r"] + 1) < 1e-9 and not t["tp1_hit"], t
    a = hand([(100, 101.5, 98.5, 100), (100, 100, 100, 100)])
    t = ny.simulate_trade(a, 0, "short", 1 / 1.2, P0)   # corto: stop 101, TP1 99; la vela toca los dos
    assert t["reason"] == "stop" and abs(t["r"] + 1) < 1e-9, t
    print("OK simulacion: si una vela toca stop y objetivo, gana el stop (largo y corto)")


def test_gap_beyond_stop_exits_at_open():
    a = hand([(100, 100.2, 99.8, 100), (98, 98.5, 97.5, 98), (98, 98, 98, 98)])
    t = ny.simulate_trade(a, 0, "long", 1 / 1.2, P0)
    assert t["reason"] == "stop" and abs(t["r"] + 2) < 1e-9, t
    print("OK simulacion: hueco mas alla del stop sale a la apertura (-2 R, no -1 R)")


def test_tp1_then_break_even():
    # vela 0 toca TP1 (101) sin tocar el stop; vela 1 vuelve a la entrada -> el resto sale en 0
    a = hand([(100, 101.2, 99.5, 101), (101, 101, 99.9, 100.2), (100, 100, 100, 100)])
    t = ny.simulate_trade(a, 0, "long", 1 / 1.2, P0)
    assert t["reason"] == "tp1+be" and t["tp1_hit"] and abs(t["r"] - 0.5) < 1e-9, t
    print("OK simulacion: 50 % en 1R y el resto en break-even = +0.5 R")


def test_tp1_then_ema_exit_at_next_open():
    ema = [50] * 3 + [102] * 5        # desde la vela 3 la EMA queda por encima de los cierres
    rows = [(100, 101.2, 99.5, 101), (101, 102, 100.8, 101.8), (101.8, 102.5, 101.5, 102.4),
            (102.4, 102.6, 101.9, 101.95), (101.5, 101.6, 101.4, 101.5), (101.5, 101.5, 101.5, 101.5)]
    a = hand(rows, ema=ema + [102] * 0)
    t = ny.simulate_trade(a, 0, "long", 1 / 1.2, P0)
    # la vela 3 cierra en 101.95 < EMA 102 -> sale en la apertura de la vela 4 (101.5): 0.5*1 + 0.5*1.5
    assert t["reason"] == "tp1+ema" and t["exit_idx"] == 4 and abs(t["r"] - 1.25) < 1e-9, t
    print("OK simulacion: el resto sale en la apertura siguiente a un cierre bajo la EMA50")


def test_time_stop_and_costs():
    rows = [(100, 100.4, 99.6, 100)] * 12
    a = hand(rows)
    p = ny.params(funding=0.0, cost_mult=1.0, max_hold_ns=3 * 15 * 60 * 10**9)   # 3 velas
    t = ny.simulate_trade(a, 0, "long", 1 / 1.2, p)
    assert t["reason"] == "time" and t["exit_idx"] == 3 and abs(t["r_gross"]) < 1e-9
    assert abs(t["cost_r"] - 2 * (ny.FEE + ny.SLIP) * 100) < 1e-9, t        # sd = 1, precio 100 -> 0.24 R
    p2 = ny.params(funding=ny.FUNDING8H, cost_mult=0.0, max_hold_ns=3 * 15 * 60 * 10**9)
    short = ny.simulate_trade(hand(rows, start="2024-03-01 07:30"), 0, "short", 1 / 1.2, p2)   # cruza las 08:00 UTC
    assert short["cost_r"] < 0, short             # el corto COBRA funding
    print("OK simulacion: cierre por tiempo, costos y funding (largo paga, corto cobra)")


def test_fixed_pct_stop():
    # entrada 100, stop fijo 1 % -> sd = 1; ignora el ATR de la senal
    a = hand([(100, 100.4, 98.9, 99.5), (99, 99, 99, 99)])
    t = ny.simulate_trade(a, 0, "long", 5.0, ny.params(funding=0.0, cost_mult=0.0, stop_pct=0.01))
    assert t["reason"] == "stop" and abs(t["stop"] - 99.0) < 1e-9 and abs(t["r"] + 1) < 1e-9, t
    print("OK simulacion: stop fijo en % del precio (1 % -> 99.0), independiente del ATR")


def test_tf_triggers():
    df = synth(40000, seed=21, drift=0.0004, vol=0.003)
    a15 = ny.prepare(df)
    for tf, hours in (("1h", 1), ("4h", 4)):
        a = ny.prepare_tf(a15, df, tf, vol_mult=0.8)
        p = ny.params(stop_pct=0.01, max_hold_ns=72 * 3600 * 10**9)
        t, _ = ny.run(a, p)
        assert len(t) > 20, (tf, len(t))
        # solo se entra en la apertura de una vela `tf` y dentro de 13:00-22:00 UTC
        assert (t["entry_ts"].dt.minute == 0).all() and (t["entry_ts"].dt.hour % hours == 0).all()
        assert ((t["entry_ts"].dt.hour >= 13) & (t["entry_ts"].dt.hour < 22)).all()
        # la EMA del trailing solo existe en el cierre de cada vela `tf`
        ends = ~np.isnan(a["ema"])
        assert ((a["mod"][ends] + 15) % (hours * 60) == 0).all()
        # sin futuro: recortar los datos no cambia las operaciones ya cerradas
        m = 30000
        cut, _ = ny.run(ny.prepare_tf(ny.prepare(df.iloc[:m]), df.iloc[:m], tf, vol_mult=0.8), p)
        lim = df["open_time"].iloc[m - 1]
        cols = ["entry_ts", "side", "entry", "stop", "reason", "r"]
        pd.testing.assert_frame_equal(t[t["exit_ts"] < lim].reset_index(drop=True)[cols],
                                      cut[cut["exit_ts"] < lim].reset_index(drop=True)[cols])
    print("OK gatillos 1h/4h: entran en la apertura de la vela siguiente, en la ventana, EMA solo al cierre y sin futuro")


def test_future_is_not_used():
    df = synth(26000, seed=7, drift=0.0004)
    m = 20000
    full, _ = ny.run(ny.prepare(df), ny.params())
    cut, _ = ny.run(ny.prepare(df.iloc[:m]), ny.params())
    limit = df["open_time"].iloc[m - 1]
    f = full[full["exit_ts"] < limit].reset_index(drop=True)
    assert len(f) > 20 and set(full["trigger"]) >= {"ruptura"}, len(f)
    cols = ["entry_ts", "side", "trigger", "entry", "stop", "reason", "r"]
    pd.testing.assert_frame_equal(f[cols], cut[cut["exit_ts"] < limit].reset_index(drop=True)[cols])
    print(f"OK senales: las {len(f)} operaciones previas al corte son identicas con y sin futuro")


def test_window_bias_and_limits():
    df = synth(30000, seed=11, drift=0.0005)
    a = ny.prepare(df)
    t, _ = ny.run(a, ny.params())
    assert len(t) > 40
    mins = t["entry_ts"].dt.hour * 60 + t["entry_ts"].dt.minute
    assert ((mins >= 13 * 60) & (mins < 22 * 60)).all(), "entrada fuera de 13:00-22:00 UTC"
    assert ((t["side"] == "long") == (t["bias"] == 1)).all(), "operacion contra el sesgo"
    for _, g in t.groupby(t["entry_ts"].dt.floor("D")):
        assert len(g) <= 3
        streak = 0
        for r in g["r"]:
            assert streak < 2, "opero despues de 2 perdidas seguidas el mismo dia"
            streak = streak + 1 if r < 0 else 0
    ex = t["exit_ts"].to_numpy()
    assert (t["entry_ts"].to_numpy()[1:] >= ex[:-1]).all(), "dos posiciones a la vez"
    print(f"OK reglas: {len(t)} operaciones, solo en 13-22 UTC, a favor del sesgo, <=3 al dia, parada tras 2 perdidas, una a la vez")


def test_bias_rules_and_no_lookahead():
    up = synth(9000, seed=2, drift=0.0015, vol=0.0005)
    a = ny.prepare(up)
    assert (a["bias"][-2000:] == 1).all()
    dn = synth(9000, seed=3, drift=-0.0015, vol=0.0005)
    assert (ny.prepare(dn)["bias"][-2000:] == -1).all()
    assert a["bias"][:96 * 20].sum() == 0                      # sin historia suficiente no hay sesgo
    d2 = up.copy()
    d2.loc[d2.index[-96:], ["open", "high", "low", "close"]] *= 0.5       # cambia solo el ultimo dia
    b2 = ny.prepare(d2)["bias"]
    assert (b2[-96:] == a["bias"][-96:]).all(), "el sesgo de hoy depende de las velas de hoy"
    print("OK sesgo: sube->largo, baja->corto, sin historia->nada y no usa las velas del propio dia")


def test_metrics_and_montecarlo():
    t = pd.DataFrame({"entry_ts": pd.date_range("2024-01-01", periods=8, freq="5D", tz="UTC"),
                      "r": [1, -1, -1, 2, -1, -1, -1, 1.0], "r_gross": [1, -1, -1, 2, -1, -1, -1, 1.0]})
    ts = t["entry_ts"].dt.tz_localize(None)
    t["month"], t["quarter"], t["year"] = ts.dt.strftime("%Y-%m"), ts.dt.to_period("Q").astype(str), ts.dt.year
    s = ny.summarize(t)
    assert s["n"] == 8 and s["streak"] == 3 and abs(s["max_dd"] - 3 * ny.RISK_PCT) < 1e-9 and abs(s["pf"] - 4 / 5) < 1e-9
    daily = np.zeros(400)
    mc = ny.monte_carlo(daily)
    assert mc["p_dd"] == 0 and mc["p_target"] == 0 and mc["p_60d"] == 1       # sin operaciones: sin maximos nuevos
    bad = ny.monte_carlo(np.full(400, -1.0))
    assert bad["p_dd"] == 1 and bad["p_target"] == 0
    print("OK metricas: racha, caida maxima, profit factor y Monte Carlo en los casos limite")


if __name__ == "__main__":
    test_stop_wins_when_stop_and_target_touch_same_candle()
    test_gap_beyond_stop_exits_at_open()
    test_tp1_then_break_even()
    test_tp1_then_ema_exit_at_next_open()
    test_time_stop_and_costs()
    test_fixed_pct_stop()
    test_tf_triggers()
    test_future_is_not_used()
    test_window_bias_and_limits()
    test_bias_rules_and_no_lookahead()
    test_metrics_and_montecarlo()
    print("\nTODAS LAS PRUEBAS DEL BACKTEST NY PASARON")
