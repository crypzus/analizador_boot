"""Estrategia BTC/USDT v1.1: sesgo diario (1D, cierre UTC) + ruptura/reversion en 15m dentro de la sesion de NY.

Reglas FIJADAS antes de ver resultados (todas las decisiones usan solo velas cerradas):
  Sesgo del dia t = f(vela 1D cerrada del dia t-1):
    largo si cierre > maximo de los 20 dias previos O retorno de 30 dias > 0;
    corto si cierre < minimo de 20 dias Y retorno de 30 dias < 0; si no, dia sin operar.
  Ventana de entradas: 08:00-17:00 Lima = 13:00-22:00 UTC (Lima es UTC-5 todo el ano). Se mide la hora de la
    apertura de la vela en la que se entra.
  Gatillos (15m, a favor del sesgo): ruptura (cierre fuera del max/min de las 12 velas previas con volumen > 1.5 x
    media de las 20 velas previas) y reversion (solo largo: tras una vela de 1h con caida cuerpo >= 2 x ATR(14) de 1h
    previo, la primera vela de 15m de las 4 siguientes que cierra sobre el maximo de la vela anterior).
  Entrada: al precio de apertura de la vela siguiente a la senal. Stop = 1.2 x ATR(14) de 15m de la vela de la senal.
  Gestion: 50 % en 1R; el resto con stop en break-even y salida cuando una vela de 15m cierra bajo (sobre) la EMA50,
    al precio de apertura de la vela siguiente. Cierre maximo a las 24 h (o a las 22:00 UTC en la variante).
  Limites: una posicion a la vez, maximo 3 operaciones al dia y parada el resto del dia UTC tras 2 perdidas seguidas.
  Si en una vela se tocan stop y objetivo, gana el stop. Si abre mas alla del stop (hueco), sale a la apertura.
  Tras tocar el objetivo, el break-even rige desde la vela siguiente (el orden dentro de esa vela no se conoce).
Costos: comision 0.05 % por lado + deslizamiento 0.01 % por lado + funding 0.01 % cada 8 h (largo paga, corto cobra).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import analysis

FEE, SLIP, FUNDING8H = 0.0005, 0.0001, 0.0001
RISK_PCT = 0.75            # % de la cuenta arriesgado por operacion, sin interes compuesto
WIN_START_MIN, WIN_END_MIN = 13 * 60, 22 * 60   # minutos UTC del dia: 13:00-22:00
H8, H24, DAY = 8 * 3600 * 10**9, 24 * 3600 * 10**9, 24 * 3600 * 10**9
MIN_1 = 60 * 10**9

DEFAULT = dict(sl_mult=1.2, lookback=12, vol_mult=1.5, drop_atr=2.0, trigger_window=4, tp1_r=1.0,
               hold_mode="h24", max_hold_ns=H24, max_trades_day=3, max_losses_day=2, min_cost_ratio=None,
               cost_mult=1.0, funding=FUNDING8H, mode="both", seed=1, stop_pct=None, window=True)


def params(**kw) -> dict:
    return {**DEFAULT, **kw}


def ts_to_ns(ts: pd.Series) -> np.ndarray:
    return ts.dt.tz_convert("UTC").dt.tz_localize(None).to_numpy().astype("datetime64[ns]").astype("int64")


def _wilder(h: pd.Series, l: pd.Series, c: pd.Series, n: int = 14) -> pd.Series:
    prev = c.shift(1)
    tr = pd.concat([h - l, (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def prepare(df: pd.DataFrame) -> dict:
    """Indicadores sobre velas 15m (columnas open_time UTC, open, high, low, close, volume)."""
    d = df.reset_index(drop=True)
    n = len(d)
    atr = _wilder(d["high"], d["low"], d["close"])
    ema = d["close"].ewm(span=50, adjust=False, min_periods=50).mean()
    vavg = d["volume"].rolling(20).mean().shift(1)           # media de las 20 velas PREVIAS
    ts_ns = ts_to_ns(d["open_time"])

    day = d.set_index("open_time").resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    up = day["close"] > day["high"].rolling(20).max().shift(1)
    dn = day["close"] < day["low"].rolling(20).min().shift(1)
    ret30 = day["close"].pct_change(30)
    raw = np.where(up | (ret30 > 0), 1, np.where(dn & (ret30 < 0), -1, 0))
    bias_day = pd.Series(raw, index=day.index).shift(1).fillna(0)   # el sesgo del dia t sale del cierre del dia t-1
    bias = bias_day.reindex(d["open_time"].dt.floor("D")).fillna(0).to_numpy()

    h1 = d.set_index("open_time").resample("1h").agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                                                       close=("close", "last"), n=("close", "count"))
    h1 = h1[h1["n"] == 4]
    atr1 = _wilder(h1["high"], h1["low"], h1["close"]).shift(1)    # ATR de 1h SIN contar la vela que cae
    pos = pd.Series(np.arange(n), index=d["open_time"])
    last_idx = pos.reindex(h1.index + pd.Timedelta(minutes=45))
    ok = last_idx.notna().to_numpy()
    mod = (d["open_time"].dt.hour * 60 + d["open_time"].dt.minute).to_numpy()
    return {"n": n, "o": d["open"].to_numpy(float), "h": d["high"].to_numpy(float), "l": d["low"].to_numpy(float),
            "c": d["close"].to_numpy(float), "v": d["volume"].to_numpy(float), "atr": atr.to_numpy(),
            "ema": ema.to_numpy(), "vavg": vavg.to_numpy(), "ts": d["open_time"], "ts_ns": ts_ns, "bias": bias,
            "bias_day": bias_day, "h1_body": (h1["open"] - h1["close"]).to_numpy()[ok],
            "h1_atr": atr1.to_numpy()[ok], "h1_last": last_idx.to_numpy()[ok].astype(int), "mod": mod}


def signals(a: dict, p: dict) -> dict:
    """Arreglos booleanos por vela de senal i (la entrada seria en i+1)."""
    n, c, h, l = a["n"], a["c"], a["h"], a["l"]
    hh = pd.Series(h).rolling(p["lookback"]).max().shift(1).to_numpy()
    ll = pd.Series(l).rolling(p["lookback"]).min().shift(1).to_numpy()
    with np.errstate(invalid="ignore"):
        volok = a["v"] > p["vol_mult"] * a["vavg"]
        brk_long = (c > hh) & volok & (a["bias"] == 1)
        brk_short = (c < ll) & volok & (a["bias"] == -1)
        drop = a["h1_body"] >= p["drop_atr"] * a["h1_atr"]
    rev = np.zeros(n, bool)
    for i0 in a["h1_last"][drop]:
        for j in range(i0 + 1, min(i0 + 1 + p["trigger_window"], n)):
            if c[j] > h[j - 1]:
                if a["bias"][j] == 1:
                    rev[j] = True
                break
    mod_next = a["mod"] + 15
    entry_ok = (mod_next >= WIN_START_MIN) & (mod_next < WIN_END_MIN)
    return {"brk_long": brk_long, "brk_short": brk_short, "rev": rev, "entry_ok": entry_ok}


def prepare_tf(a: dict, df: pd.DataFrame, tf: str, lookback: int = 12, vol_mult: float = 1.5) -> dict:
    """Variante con gatillos en velas de `tf` ('1h' o '4h'): la ruptura y la EMA50 se calculan en esas velas, pero la
    operacion se simula con las velas de 15m (mejor resolucion para stop y objetivo). La senal cae en la ultima vela de
    15m de cada vela `tf`, asi que se entra en la apertura de la vela `tf` siguiente. El trailing solo se evalua en
    el cierre de cada vela `tf`."""
    d = df.reset_index(drop=True)
    step = int(pd.Timedelta(tf) / pd.Timedelta(minutes=15))
    bars = d.set_index("open_time").resample(tf).agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                                                     close=("close", "last"), volume=("volume", "sum"), n=("close", "count"))
    bars = bars[bars["n"] == step]
    n = a["n"]
    pos = pd.Series(np.arange(n), index=d["open_time"])
    end = pos.reindex(bars.index + pd.Timedelta(tf) - pd.Timedelta(minutes=15))
    ok = end.notna().to_numpy()
    idx = end.to_numpy()[ok].astype(int)
    hh = bars["high"].rolling(lookback).max().shift(1).to_numpy()[ok]
    ll = bars["low"].rolling(lookback).min().shift(1).to_numpy()[ok]
    vavg = bars["volume"].rolling(20).mean().shift(1).to_numpy()[ok]
    close, vol = bars["close"].to_numpy()[ok], bars["volume"].to_numpy()[ok]
    with np.errstate(invalid="ignore"):
        volok = vol > vol_mult * vavg
        bl, bs = (close > hh) & volok, (close < ll) & volok
    brk_long, brk_short = np.zeros(n, bool), np.zeros(n, bool)
    brk_long[idx] = bl & (a["bias"][idx] == 1)
    brk_short[idx] = bs & (a["bias"][idx] == -1)
    ema_tf = np.full(n, np.nan)
    ema_tf[idx] = bars["close"].ewm(span=50, adjust=False, min_periods=50).mean().to_numpy()[ok]
    mod_next = a["mod"] + 15
    sig = {"brk_long": brk_long, "brk_short": brk_short, "rev": np.zeros(n, bool),
           "entry_ok": (mod_next >= WIN_START_MIN) & (mod_next < WIN_END_MIN)}
    return {**a, "ema": ema_tf, "tf_sig": sig, "tf": tf}


def _n8(t0: int, t1: int) -> int:
    return int(t1 // H8 - t0 // H8)


def simulate_trade(a: dict, e: int, side: str, atr_sig: float, p: dict) -> dict | None:
    """Simula una operacion que entra en la apertura de la vela e. None si los datos terminan antes de cerrarla."""
    o, h, l, c, ema, ts = a["o"], a["h"], a["l"], a["c"], a["ema"], a["ts_ns"]
    n, s = a["n"], 1 if side == "long" else -1
    entry = o[e]
    sd = p["stop_pct"] * entry if p.get("stop_pct") else p["sl_mult"] * atr_sig   # distancia fija en % o en ATR
    stop, tp1 = entry - s * sd, entry + s * p["tp1_r"] * sd
    if p["hold_mode"] == "session_end":
        limit = ts[e] - ts[e] % DAY + WIN_END_MIN * MIN_1
    else:
        limit = ts[e] + p["max_hold_ns"]
    last = int(np.searchsorted(ts, limit, side="left"))
    stage, rem, tp_idx, pending, parts, reason, exit_idx = 1, 1.0, None, False, [], None, None
    k = e
    while k < n:
        if k >= last:
            parts.append((rem, o[k])); reason = "time" if stage == 1 else "tp1+time"; exit_idx = k; break
        if pending:
            parts.append((rem, o[k])); reason = "tp1+ema"; exit_idx = k; break
        if stage == 1:
            if k > e and (o[k] - stop) * s <= 0:                       # hueco mas alla del stop
                parts.append((1.0, o[k])); reason = "stop"; exit_idx = k; break
            if (l[k] <= stop) if s == 1 else (h[k] >= stop):           # el stop gana si tambien se toca el objetivo
                parts.append((1.0, stop)); reason = "stop"; exit_idx = k; break
            if (h[k] >= tp1) if s == 1 else (l[k] <= tp1):
                fill = max(tp1, o[k]) if s == 1 else min(tp1, o[k])
                parts.append((0.5, fill)); stage, rem, tp_idx = 2, 0.5, k
                if not np.isnan(ema[k]) and (c[k] - ema[k]) * s < 0:
                    pending = True
        else:
            if (o[k] - entry) * s <= 0:
                parts.append((rem, o[k])); reason = "tp1+be"; exit_idx = k; break
            if (l[k] <= entry) if s == 1 else (h[k] >= entry):
                parts.append((rem, entry)); reason = "tp1+be"; exit_idx = k; break
            if not np.isnan(ema[k]) and (c[k] - ema[k]) * s < 0:
                pending = True
        k += 1
    if exit_idx is None:
        return None
    unit = (FEE + SLIP) * p["cost_mult"]
    gross = sum(f * s * (px - entry) for f, px in parts)
    cost = unit * entry + sum(f * unit * px for f, px in parts)
    t_tp = ts[tp_idx] if tp_idx is not None else ts[exit_idx]
    fund = s * p["funding"] * entry * (_n8(ts[e], t_tp) + 0.5 * _n8(t_tp, ts[exit_idx]) if tp_idx is not None
                                       else _n8(ts[e], ts[exit_idx]))
    return {"side": side, "entry": entry, "stop": stop, "tp1": tp1, "sd": sd, "exit_idx": exit_idx,
            "entry_idx": e, "reason": reason, "r_gross": gross / sd, "cost_r": (cost + fund) / sd,
            "r": (gross - cost - fund) / sd, "hold_h": (ts[exit_idx] - ts[e]) / 3.6e12, "tp1_hit": tp_idx is not None}


def _candidates(a: dict, sig: dict, p: dict) -> list[tuple[int, str, str]]:
    n, mode, ok = a["n"], p["mode"], sig["entry_ok"]
    out: list[tuple[int, str, str]] = []
    if mode in ("both", "brk", "rev"):
        for i in np.nonzero((sig["brk_long"] | sig["brk_short"] | sig["rev"]) & ok)[0]:
            if mode != "rev" and sig["brk_long"][i]:
                out.append((int(i), "long", "ruptura"))
            elif mode != "rev" and sig["brk_short"][i]:
                out.append((int(i), "short", "ruptura"))
            elif mode != "brk" and sig["rev"][i]:
                out.append((int(i), "long", "reversion"))
    elif mode == "bias_only":
        for i in np.nonzero(((a["mod"] + 15) == WIN_START_MIN) & (a["bias"] != 0))[0]:   # entrada a las 13:00 UTC
            out.append((int(i), "long" if a["bias"][i] == 1 else "short", "sesgo"))
    elif mode == "random":
        rng = np.random.default_rng(p["seed"])
        days = pd.Series(np.arange(n)).groupby(a["ts"].dt.tz_localize(None).dt.floor("D").to_numpy())
        for _, idx in days:
            idx = idx.to_numpy()
            idx = idx[ok[idx] & (a["bias"][idx] != 0)]
            if len(idx):
                i = int(rng.choice(idx))
                out.append((i, "long" if a["bias"][i] == 1 else "short", "azar"))
    return sorted(out)


def run(a: dict, p: dict) -> tuple[pd.DataFrame, dict]:
    sig = a["tf_sig"] if "tf_sig" in a else signals(a, p)
    if not p.get("window", True):                       # variante informativa: sin ventana horaria
        sig = {**sig, "entry_ok": np.ones(a["n"], bool)}
    counts = {"senales": 0, "ocupado": 0, "max_dia": 0, "parada_perdidas": 0, "costo_alto": 0, "sin_atr": 0, "sin_cerrar": 0}
    state: dict[int, list[int]] = {}      # dia UTC -> [operaciones, perdidas seguidas]
    trades, busy_until = [], -1
    for i, side, trig in _candidates(a, sig, p):
        counts["senales"] += 1
        e = i + 1
        if e >= a["n"]:
            continue
        if e <= busy_until:
            counts["ocupado"] += 1; continue
        atr = a["atr"][i]
        if np.isnan(atr):
            counts["sin_atr"] += 1; continue
        day = int(a["ts_ns"][e] // DAY)
        st = state.setdefault(day, [0, 0])
        if st[0] >= p["max_trades_day"]:
            counts["max_dia"] += 1; continue
        if st[1] >= p["max_losses_day"]:
            counts["parada_perdidas"] += 1; continue
        sd = p["stop_pct"] * a["o"][e] if p.get("stop_pct") else p["sl_mult"] * atr
        if p["min_cost_ratio"] and 2 * (FEE + SLIP) * p["cost_mult"] * a["o"][e] / sd > p["min_cost_ratio"]:
            counts["costo_alto"] += 1; continue
        t = simulate_trade(a, e, side, atr, p)
        if t is None:
            counts["sin_cerrar"] += 1; continue
        st[0] += 1
        st[1] = st[1] + 1 if t["r"] < 0 else 0
        busy_until = t["exit_idx"]
        t.update(trigger=trig, entry_ts=a["ts"].iloc[e], exit_ts=a["ts"].iloc[t["exit_idx"]], bias=int(a["bias"][i]))
        trades.append(t)
    df = pd.DataFrame(trades)
    if len(df):
        ts = df["entry_ts"].dt.tz_localize(None)
        df["month"], df["quarter"], df["year"] = ts.dt.strftime("%Y-%m"), ts.dt.to_period("Q").astype(str), ts.dt.year
        df = df.sort_values("entry_ts").reset_index(drop=True)
    return df, counts


# ----------------------------------------------------------------------------- metricas
def equity_pct(r: np.ndarray) -> np.ndarray:
    return np.cumsum(r) * RISK_PCT


def summarize(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"n": 0}
    r = df.sort_values("entry_ts")["r"].to_numpy()
    w, ls = r[r > 0], r[r < 0]
    eq = np.r_[0.0, equity_pct(r)]
    dd = float((np.maximum.accumulate(eq) - eq).max())
    streak = best = 0
    for x in r:
        streak = streak + 1 if x < 0 else 0
        best = max(best, streak)
    lo, hi = analysis.boot_ci(df, "r")
    q = df.groupby("quarter")["r"].agg(["sum", "count"])
    q = q[q["count"] >= 5]
    yr = df.groupby("year")["r"].sum()
    rr = (w.mean() / abs(ls.mean())) if len(w) and len(ls) else float("nan")
    return {"n": len(r), "win": float((r > 0).mean()), "avg_win": float(w.mean()) if len(w) else float("nan"),
            "avg_loss": float(ls.mean()) if len(ls) else float("nan"), "rr": float(rr), "e": float(r.mean()),
            "pf": float(w.sum() / abs(ls.sum())) if len(ls) and ls.sum() < 0 else float("inf"),
            "total_r": float(r.sum()), "total_pct": float(eq[-1]), "gross_r": float(df["r_gross"].sum()),
            "max_dd": dd, "streak": best, "streak_pct": best * RISK_PCT, "lo": lo, "hi": hi,
            "q_pos": int((q["sum"] > 0).sum()), "q_n": len(q), "years": {int(k): float(v) for k, v in yr.items()},
            "be_win": float(1 / (1 + rr)) if rr == rr else float("nan")}


def daily_r(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> np.ndarray:
    days = pd.date_range(start.floor("D").tz_localize(None), end.floor("D").tz_localize(None), freq="D")
    s = df.assign(day=df["entry_ts"].dt.tz_localize(None).dt.floor("D")).groupby("day")["r"].sum()
    return s.reindex(days, fill_value=0.0).to_numpy()


def monte_carlo(daily: np.ndarray, days: int = 90, n: int = 10_000, seed: int = 1, dd_limit: float = 20.0,
                target: float = 10.0, daily_limit: float = 5.0) -> dict:
    """Caminos de `days` dias remuestreando dias reales (los dias sin operacion cuentan 0). Solo usa operaciones propias."""
    rng = np.random.default_rng(seed)
    x = daily[rng.integers(0, len(daily), (n, days))] * RISK_PCT
    eq = np.cumsum(x, axis=1)
    prev_peak = np.maximum.accumulate(np.concatenate([np.zeros((n, 1)), eq], axis=1), axis=1)
    dd = prev_peak[:, 1:] - eq
    breach, reach = dd > dd_limit, eq >= target
    t_b = np.where(breach.any(1), breach.argmax(1), days + 1)
    t_r = np.where(reach.any(1), reach.argmax(1), days + 1)
    cnt, longest = np.zeros(n), np.zeros(n)
    for d in range(days):
        new_high = eq[:, d] > prev_peak[:, d]
        cnt = np.where(new_high, 0, cnt + 1)
        longest = np.maximum(longest, cnt)
    return {"p_dd": float(breach.any(1).mean()), "p_target": float(reach.any(1).mean()),
            "p_target_first": float((t_r < t_b).mean()), "p_daily": float((x <= -daily_limit).any(1).mean()),
            "p_60d": float((longest >= 60).mean())}


def walk_forward(by_combo: dict[str, pd.DataFrame], default: str, train: int = 6, test: int = 2, min_train: int = 30) -> dict:
    """Ventanas moviles: elige en `train` meses la combinacion con mejor esperanza (en R) y la opera los `test` meses
    siguientes. `sel` se queda fuera si la mejor no es positiva; `sel_all` opera siempre la mejor."""
    pm = {k: v.assign(pm=v["entry_ts"].dt.tz_localize(None).dt.to_period("M")) for k, v in by_combo.items() if len(v)}
    first = min(v["pm"].min() for v in pm.values())
    last = max(v["pm"].max() for v in pm.values())
    sel, sel_all, fixed, dec, pool = [], [], [], [], []
    s = first
    while s + train + test - 1 <= last:
        tr, te = (s, s + train - 1), (s + train, s + train + test - 1)
        best, best_e = None, -np.inf
        for k, v in pm.items():
            x = v[(v["pm"] >= tr[0]) & (v["pm"] <= tr[1])]
            if len(x) >= min_train and x["r"].mean() > best_e:
                best, best_e = k, x["r"].mean()
        for k, v in pm.items():
            x = v[(v["pm"] >= te[0]) & (v["pm"] <= te[1])]
            if k == default:
                fixed.append(x)
            pool.append(x.assign(combo=k))
            if k == best:
                sel_all.append(x)
                if best_e > 0:
                    sel.append(x)
        dec.append({"test_from": str(te[0]), "test_to": str(te[1]), "combo": best or "-", "train_e": best_e if best else np.nan,
                    "stay_out": bool(best is None or best_e <= 0)})
        s += test
    cat = lambda xs: pd.concat(xs, ignore_index=True) if xs else pd.DataFrame()  # noqa: E731
    return {"sel": cat(sel), "sel_all": cat(sel_all), "fixed": cat(fixed), "pool": cat(pool), "decisions": pd.DataFrame(dec)}
