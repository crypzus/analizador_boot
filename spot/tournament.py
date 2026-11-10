"""
Torneo SPOT (solo compras/ventas, sin apalancamiento, sin cortos) con SOL y ADA, 50% del capital en cada una.
Precios: futuros perpetuos de Binance como aproximacion del spot (la base es minima).
Reglas fijadas antes de ver resultados:
  - Estrategias: BH (comprar y mantener), TREND (Donchian 20/10), RSI (comprar RSI14<30, vender RSI>50, stop -15%),
    BOLL (comprar bajo banda inferior 20/2, vender en la media, stop -10%),
    DIP (comprar 20% bajo el maximo de 60 dias, vender +15%, stop -15% o 30 dias),
    GRID (20 niveles geometricos, rango de 90 dias, regrid cada 60 dias).
  - Senal al cierre del dia t, ejecucion en la apertura de t+1. Comision 0.10% + slippage 0.05% por lado
    (el grid usa 0.10% de comision y 0.05% solo al liquidar en el regrid).
  - Ventana principal: ultimos 12 meses. Ventanas de control: 2023, 2024, 2025, 2026 (hasta hoy) y 2023-hoy.
Uso:  python spot/tournament.py
"""
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(__file__).parent.parent / "user_data" / "data" / "binance" / "futures"
FEE, SLIP = 0.001, 0.0005
COST = FEE + SLIP
ASSETS = ["SOL", "ADA"]


def load15(a):
    d = pd.read_feather(DATA / f"{a}_USDT_USDT-15m-futures.feather")
    d["date"] = pd.to_datetime(d["date"], utc=True)
    return d.set_index("date")[["open", "high", "low", "close", "volume"]]


def rs(d, rule):
    return d.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()


# ---------------------------------------------------------------- estrategias diarias -> posicion deseada (0/1)
def sig_bh(d):
    return np.ones(len(d))


def sig_trend(d, n=20, m=10):
    c = d["close"]
    hh, ll = c.shift(1).rolling(n).max().values, c.shift(1).rolling(m).min().values
    cv, pos, cur = c.values, np.zeros(len(d)), 0.0
    for t in range(len(d)):
        if cur == 0 and cv[t] > hh[t]:
            cur = 1.0
        elif cur == 1 and cv[t] < ll[t]:
            cur = 0.0
        pos[t] = cur
    return pos


def rsi14(c, n=14):
    dl = c.diff()
    up = dl.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-dl.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, 1e-12))


def sig_rsi(d):
    c, r = d["close"].values, rsi14(d["close"]).values
    pos, cur, ent = np.zeros(len(d)), 0.0, 0.0
    for t in range(len(d)):
        if cur == 0 and r[t] < 30:
            cur, ent = 1.0, c[t]
        elif cur == 1 and (r[t] > 50 or c[t] < ent * 0.85):
            cur = 0.0
        pos[t] = cur
    return pos


def sig_boll(d):
    c = d["close"]
    ma, sd = c.rolling(20).mean().values, c.rolling(20).std().values
    cv, pos, cur, ent = c.values, np.zeros(len(d)), 0.0, 0.0
    for t in range(len(d)):
        if cur == 0 and cv[t] < ma[t] - 2 * sd[t]:
            cur, ent = 1.0, cv[t]
        elif cur == 1 and (cv[t] > ma[t] or cv[t] < ent * 0.90):
            cur = 0.0
        pos[t] = cur
    return pos


def sig_dip(d):
    c = d["close"]
    mx = c.rolling(60).max().values
    cv, pos, cur, ent, t0 = c.values, np.zeros(len(d)), 0.0, 0.0, 0
    for t in range(len(d)):
        if cur == 0 and cv[t] <= mx[t] * 0.80:
            cur, ent, t0 = 1.0, cv[t], t
        elif cur == 1 and (cv[t] >= ent * 1.15 or cv[t] <= ent * 0.85 or t - t0 >= 30):
            cur = 0.0
        pos[t] = cur
    return pos


DAILY = {"BH": sig_bh, "TREND": sig_trend, "RSI": sig_rsi, "BOLL": sig_boll, "DIP": sig_dip}


def daily_series(d, sig):
    """retornos diarios abierto->abierto con costes por cambio de posicion, y operaciones cerradas."""
    pos = pd.Series(sig(d), index=d.index).shift(1).fillna(0.0)
    r = d["open"].shift(-1) / d["open"] - 1
    turn = pos.diff().abs().fillna(pos.abs())
    ret = pos * r - turn * COST
    seg = (pos != pos.shift(1)).cumsum()
    trades = []
    for _, g in pd.DataFrame({"p": pos, "r": r}).groupby(seg):
        if g["p"].iloc[0] == 1.0 and g.index[-1] != d.index[-1]:
            trades.append((g.index[0], np.prod(1 + g["r"].fillna(0)) - 1 - 2 * COST))
    return ret.dropna(), trades


# ---------------------------------------------------------------- grid spot sobre 1h
def grid_series(h1, n=20, look=90, regrid=60, fee=FEE):
    """equity diaria normalizada y ciclos (compra->venta) completados."""
    t = h1.index
    o, h, l, c = h1["open"].values, h1["high"].values, h1["low"].values, h1["close"].values
    start = look * 24
    cash, coins = 1.0, np.zeros(n)
    buy_px = np.zeros(n)
    levels, slot_cash = None, 0.0
    eq = np.full(len(h1), np.nan)
    cycles = []
    next_reset = start
    for i in range(start, len(h1)):
        if i >= next_reset or levels is None:
            cash += coins.sum() * c[i - 1] * (1 - fee - SLIP)
            coins[:] = 0
            lo, hi = l[i - look * 24:i].min(), h[i - look * 24:i].max()
            levels = np.geomspace(lo, hi, n + 1)
            slot_cash = cash / n
            next_reset = i + regrid * 24
        for k in range(n):  # ventas primero (sin ida y vuelta dentro de la misma vela)
            if coins[k] > 0 and h[i] >= levels[k + 1]:
                px = max(o[i], levels[k + 1])
                cash += coins[k] * px * (1 - fee)
                cycles.append((t[i], px / buy_px[k] - 1 - 2 * fee))
                coins[k] = 0
        for k in range(n):
            if coins[k] == 0 and l[i] <= levels[k] and cash >= slot_cash * 0.999:
                px = min(o[i], levels[k])
                spend = min(slot_cash, cash)
                coins[k] = spend * (1 - fee) / px
                buy_px[k] = px
                cash -= spend
        eq[i] = cash + coins.sum() * c[i]
    e = pd.Series(eq, index=t).dropna()
    e = e.groupby(e.index.floor("1D")).last()
    return e, cycles


def evaluate(eq_total: pd.Series, lo, hi):
    e = eq_total.loc[lo:hi]
    e = e / e.iloc[0]
    return e.iloc[-1] - 1, (e / e.cummax() - 1).min()


def main():
    data15 = {a: load15(a) for a in ASSETS}
    day = {a: rs(data15[a], "1D").iloc[:-1] for a in ASSETS}   # sin el dia incompleto
    h1 = {a: rs(data15[a], "1h") for a in ASSETS}
    last = day["SOL"].index[-1]
    ts = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731
    windows = {
        "ULT.12M": (last - pd.Timedelta(days=364), last),
        "2023": (ts("2023-01-01"), ts("2023-12-31")),
        "2024": (ts("2024-01-01"), ts("2024-12-31")),
        "2025": (ts("2025-01-01"), ts("2025-12-31")),
        "2026YTD": (ts("2026-01-01"), last),
        "2023-hoy": (ts("2023-01-01"), last),
    }
    eqs, trades = {}, {}
    for name, sig in DAILY.items():
        parts, tr = [], []
        for a in ASSETS:
            ret, t = daily_series(day[a], sig)
            parts.append((1 + ret).cumprod())
            tr += t
        eqs[name] = 0.5 * parts[0] + 0.5 * parts[1]
        trades[name] = tr
    parts, cyc = [], []
    for a in ASSETS:
        e, c = grid_series(h1[a])
        parts.append(e / e.iloc[0])
        cyc += c
    idx = parts[0].index.intersection(parts[1].index)
    eqs["GRID"] = 0.5 * parts[0].loc[idx] + 0.5 * parts[1].loc[idx]
    trades["GRID"] = cyc

    rows = []
    for w, (lo, hi) in windows.items():
        for name, e in eqs.items():
            r, dd = evaluate(e, lo, hi)
            tr = [x for (tt, x) in trades[name] if lo <= tt <= hi]
            rows.append(dict(ventana=w, estrategia=name, ret_pct=round(100 * r, 1), dd_pct=round(100 * dd, 1),
                             operaciones=len(tr) if name != "BH" else 1,
                             win_pct=round(100 * np.mean([x > 0 for x in tr]), 0) if tr and name != "BH" else np.nan,
                             usd_de_100=round(100 * (1 + r), 1)))
    df = pd.DataFrame(rows)
    pd.set_option("display.width", 200)
    for w in windows:
        print(f"\n===== {w}  ({windows[w][0].date()} -> {windows[w][1].date()})  capital 100 USDT, 50/50 SOL y ADA")
        print(df[df.ventana == w].drop(columns="ventana").sort_values("ret_pct", ascending=False).to_string(index=False))
    print("\n===== ULT.12M precio de cada activo (comprar y mantener)")
    lo, hi = windows["ULT.12M"]
    for a in ASSETS:
        c = day[a]["open"].loc[lo:hi]
        print(a, round(100 * (c.iloc[-1] / c.iloc[0] - 1), 1), "%  | dd", round(100 * (c / c.cummax() - 1).min(), 1), "%")


if __name__ == "__main__":
    main()
