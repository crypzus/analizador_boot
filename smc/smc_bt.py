"""
Port a Python de "SMC Entradas 15M - Estrategia (4H - 1H - 15M)" (Pine v5) para backtest con
datos de Binance Futures. Simulador propio, barra a barra, que imita el orden de operaciones
del Strategy Tester de TradingView:

  * La senal se evalua al CIERRE de la vela i; la entrada se llena en la APERTURA de i+1.
  * SL/TP (strategy.exit) se activan desde la vela de entrada. Si una vela toca ambos,
    se usa la heuristica de TradingView (camino O->H->L->C si la apertura esta mas cerca del
    maximo, si no O->L->H->C). Gaps: el stop se llena en la apertura.
  * Comision por lado y slippage (en % del precio) en entradas y stops. TP como limite sin slippage.
  * Un tamano por riesgo fijo (risk_pct) con tope de apalancamiento.

Diferencias conocidas / aproximaciones (ver README_SMC.md):
  * Pivotes: se acepta empate (el pivote es el maximo de la ventana, no estrictamente mayor).
  * ATR/EMA: ewm sin semilla SMA (diferencia irrelevante tras el calentamiento).
  * request.security: htf_mode="tv" replica el comportamiento de lookahead_off + [1]
    (valores de la vela HTF cerrada mas el retraso extra del [1]); "simple" usa solo el retraso [1].
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict, replace

import numpy as np
import pandas as pd

NAN = float("nan")


@dataclass
class Params:
    # 1 - direccion y patron
    use_bias: bool = True
    bias_ema_len: int = 50
    use_struct: bool = True
    pivot_len_htf: int = 5
    choch_only: bool = False
    htf_break_max: int = 24
    # 2 - disparo 15m
    atr_len: int = 14
    pivot_len: int = 5
    fvg_min_atr: float = 0.5
    disp_mult: float = 1.0
    use_vol: bool = True
    vol_len: int = 20
    vol_mult: float = 1.2
    ob_lookback: int = 10
    use_sweep: bool = True
    sweep_lookback: int = 20
    setup_expire: int = 40
    cooldown_bars: int = 6
    # 3 - SL / TP
    sl_buffer_atr: float = 0.25
    sl_min_atr: float = 0.30
    sl_max_atr: float = 3.0
    tp_mode: str = "liq"  # "liq" | "fixed"
    fixed_rr: float = 2.0
    min_rr: float = 2.0
    # 5 - rango
    use_range: bool = True
    range_len: int = 96
    range_max_atr: float = 14.0
    range_edge: float = 0.35
    range_tp_mode: str = "mid"  # "mid" | "opposite"
    range_min_rr: float = 1.5
    # 6 - barrido
    use_sw: bool = True
    sw_allow_long: bool = True
    sw_allow_short: bool = True
    sw_touches: int = 2
    sw_tol_atr: float = 0.3
    sw_min_wick: float = 0.1
    sw_use_vol: bool = True
    sw_strong_close: bool = True
    sw_counter_strict: bool = True
    # 7 - riesgo
    risk_pct: float = 1.0
    max_leverage: float = 13.0
    max_trades_day: int = 5
    use_daily_loss: bool = True
    max_daily_loss_pct: float = 3.0
    sl_buffer_rng_atr: float = 0.25
    # simulacion
    capital: float = 100.0
    fee: float = 0.0005     # por lado (0.05% taker)
    slip: float = 0.0002    # 0.02% en entradas y stops
    htf_mode: str = "tv"    # "tv" | "simple"
    enable_ob: bool = True   # False = solo variante barrido
    cost_aware: bool = False # R:R neto de comisiones y slippage
    warmup: int = 400


# ----------------------------------------------------------------------------------------
# Datos y HTF
# ----------------------------------------------------------------------------------------
def load_ohlcv(path: str) -> pd.DataFrame:
    df = pd.read_feather(path)
    df = df[["date", "open", "high", "low", "close", "volume"]].copy()
    df["date"] = pd.to_datetime(df["date"], utc=True)
    return df.reset_index(drop=True)


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    r = df.set_index("date").resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    return r.dropna()


def pivots(arr: np.ndarray, left: int, right: int, is_high: bool) -> np.ndarray:
    """Valor del pivote en la barra de CONFIRMACION (j): pivote de la barra j-right. NaN si no hay."""
    s = pd.Series(arr)
    w = left + right + 1
    ext = s.rolling(w).max() if is_high else s.rolling(w).min()
    center = s.shift(right)
    out = np.where(center.values == ext.values, center.values, np.nan)
    return out


def htf_struct(h: np.ndarray, l: np.ndarray, c: np.ndarray, plen: int):
    """f_struct() de Pine por barra HTF (sin el [1] final). Devuelve dir, choch, age."""
    ph = pivots(h, plen, plen, True)
    pl = pivots(l, plen, plen, False)
    n = len(c)
    d = np.zeros(n)
    ch = np.zeros(n)
    age = np.full(n, 9999.0)
    sw_h = sw_l = NAN
    sw_h_brk = sw_l_brk = True
    trend = 0
    last_dir = 0
    last_choch = False
    last_bar = None
    for j in range(n):
        if not math.isnan(ph[j]):
            sw_h, sw_h_brk = ph[j], False
        if not math.isnan(pl[j]):
            sw_l, sw_l_brk = pl[j], False
        bull = (not math.isnan(sw_h)) and (not sw_h_brk) and c[j] > sw_h
        bear = (not math.isnan(sw_l)) and (not sw_l_brk) and c[j] < sw_l
        if bull:
            last_choch = trend == -1
            trend = 1
            last_dir = 1
            last_bar = j
            sw_h_brk = True
        elif bear:
            last_choch = trend == 1
            trend = -1
            last_dir = -1
            last_bar = j
            sw_l_brk = True
        d[j] = last_dir
        ch[j] = 1.0 if last_choch else 0.0
        age[j] = 9999.0 if last_bar is None else j - last_bar
    return d, ch, age


def map_htf(df15: pd.DataFrame, htf_index: pd.DatetimeIndex, series: np.ndarray, rule: str,
            htf_minutes: int, mode: str) -> np.ndarray:
    """Valor de una serie HTF 'raw' (calculada por barra HTF) tal como la veria el script en
    cada vela de 15m con  request.security(..., f()[1], lookahead_off)."""
    s = pd.Series(series, index=htf_index)
    shifted = s.shift(1)  # el [1] dentro de la funcion
    key = df15["date"].dt.floor(rule)
    v_last = shifted.reindex(key).values
    if mode == "simple":
        return v_last
    v_prev = shifted.shift(1).reindex(key).values
    is_last = ((df15["date"] + pd.Timedelta(minutes=15)) == (key + pd.Timedelta(minutes=htf_minutes))).values
    return np.where(is_last, v_last, v_prev)


def build_context(df: pd.DataFrame, p: Params) -> dict:
    h4 = resample_ohlcv(df, "4h")
    h1 = resample_ohlcv(df, "1h")
    ema = h4["close"].ewm(span=p.bias_ema_len, adjust=False).mean()
    bias_raw = np.where(h4["close"].values > ema.values, 1.0, -1.0)
    bias = map_htf(df, h4.index, bias_raw, "4h", 240, p.htf_mode)
    d, ch, age = htf_struct(h1["high"].values, h1["low"].values, h1["close"].values, p.pivot_len_htf)
    h1_dir = map_htf(df, h1.index, d, "1h", 60, p.htf_mode)
    h1_choch = map_htf(df, h1.index, ch, "1h", 60, p.htf_mode)
    h1_age = map_htf(df, h1.index, age, "1h", 60, p.htf_mode)
    return dict(bias=bias, h1_dir=h1_dir, h1_choch=h1_choch, h1_age=h1_age)


# ----------------------------------------------------------------------------------------
# Backtest
# ----------------------------------------------------------------------------------------
def run_backtest(df: pd.DataFrame, p: Params, ctx: dict | None = None) -> pd.DataFrame:
    if ctx is None:
        ctx = build_context(df, p)
    o = df["open"].values
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    v = df["volume"].values
    n = len(df)
    days = df["date"].dt.floor("1D").values

    prev_c = np.r_[c[0], c[:-1]]
    tr = np.maximum.reduce([h - l, np.abs(h - prev_c), np.abs(l - prev_c)])
    tr[0] = h[0] - l[0]
    atr = pd.Series(tr).ewm(alpha=1.0 / p.atr_len, adjust=False).mean().values
    vol_ma = pd.Series(v).rolling(p.vol_len).mean().values
    rng_hi = pd.Series(h).rolling(p.range_len).max().values
    rng_lo = pd.Series(l).rolling(p.range_len).min().values
    ph = pivots(h, p.pivot_len, p.pivot_len, True)
    pl = pivots(l, p.pivot_len, p.pivot_len, False)
    body = np.abs(c - o)

    bias, h1_dir, h1_choch, h1_age = ctx["bias"], ctx["h1_dir"], ctx["h1_choch"], ctx["h1_age"]

    equity = p.capital
    pos = None
    pending = None
    trades = []

    last_ph = last_pl = NAN
    last_sw_low = last_sw_high = None
    liq_h: list[float] = []
    liq_l: list[float] = []
    setups: list[dict] = []
    last_bull_ob = last_bear_ob = None
    last_signal_bar = None
    trades_today = 0
    day_eq0 = None
    cur_day = None
    fee, slip = p.fee, p.slip

    def mtm(i):
        if pos is None:
            return equity
        d = 1 if pos["side"] == "L" else -1
        return equity + pos["qty"] * (c[i] - pos["entry"]) * d

    def close_pos(i, px, reason):
        nonlocal equity, pos
        d = 1 if pos["side"] == "L" else -1
        gross = pos["qty"] * (px - pos["entry"]) * d
        fees = pos["entry_fee"] + fee * pos["qty"] * px
        pnl = gross - fees
        equity += pnl - 0.0  # la comision de entrada ya se descuenta al cierre junto a la de salida
        trades.append(dict(
            side=pos["side"], tag=pos["tag"], entry_time=df["date"].iat[pos["open_i"]], exit_time=df["date"].iat[i],
            entry=pos["entry"], exit=px, sl=pos["sl"], tp=pos["tp"], qty=pos["qty"], pnl=pnl,
            r=pnl / pos["risk_amt"] if pos["risk_amt"] > 0 else NAN, reason=reason, bars=i - pos["open_i"],
        ))
        pos = None

    for i in range(n):
        # ---- 1) entrada pendiente: se llena en la apertura de esta vela
        if pending is not None:
            d = 1 if pending["side"] == "L" else -1
            fill = o[i] * (1 + slip * d)
            pos = dict(pending, entry=fill, open_i=i, entry_fee=fee * pending["qty"] * fill)
            pending = None

        # ---- 2) salidas durante la vela
        if pos is not None:
            sl, tp = pos["sl"], pos["tp"]
            if pos["side"] == "L":
                if o[i] <= sl:
                    close_pos(i, o[i] * (1 - slip), "sl_gap")
                elif o[i] >= tp:
                    close_pos(i, o[i], "tp_gap")
                else:
                    sl_hit, tp_hit = l[i] <= sl, h[i] >= tp
                    if sl_hit and tp_hit:
                        if abs(h[i] - o[i]) <= abs(o[i] - l[i]):
                            close_pos(i, tp, "tp")
                        else:
                            close_pos(i, sl * (1 - slip), "sl")
                    elif sl_hit:
                        close_pos(i, sl * (1 - slip), "sl")
                    elif tp_hit:
                        close_pos(i, tp, "tp")
            else:
                if o[i] >= sl:
                    close_pos(i, o[i] * (1 + slip), "sl_gap")
                elif o[i] <= tp:
                    close_pos(i, o[i], "tp_gap")
                else:
                    sl_hit, tp_hit = h[i] >= sl, l[i] <= tp
                    if sl_hit and tp_hit:
                        if abs(h[i] - o[i]) <= abs(o[i] - l[i]):
                            close_pos(i, sl * (1 + slip), "sl")
                        else:
                            close_pos(i, tp, "tp")
                    elif sl_hit:
                        close_pos(i, sl * (1 + slip), "sl")
                    elif tp_hit:
                        close_pos(i, tp, "tp")

        # ---- 3) contadores diarios
        if days[i] != cur_day:
            cur_day = days[i]
            trades_today = 0
            day_eq0 = mtm(i)

        # ---- 4) estado del cierre de la vela i
        if not math.isnan(ph[i]):
            last_ph = ph[i]
            liq_h.append(ph[i])
            if len(liq_h) > 25:
                liq_h.pop(0)
        if not math.isnan(pl[i]):
            last_pl = pl[i]
            liq_l.append(pl[i])
            if len(liq_l) > 25:
                liq_l.pop(0)
        liq_h = [x for x in liq_h if h[i] < x]
        liq_l = [x for x in liq_l if l[i] > x]

        if not math.isnan(last_pl) and l[i] < last_pl and c[i] > last_pl:
            last_sw_low = i
        if not math.isnan(last_ph) and h[i] > last_ph and c[i] < last_ph:
            last_sw_high = i

        if i < p.warmup or math.isnan(atr[i]) or math.isnan(vol_ma[i]) or math.isnan(rng_hi[i]):
            continue

        a = atr[i]
        cp = c[i] * (2 * fee + 1.5 * slip) if p.cost_aware else 0.0
        sweep_ok_long = (not p.use_sweep) or (last_sw_low is not None and i - last_sw_low <= p.sweep_lookback)
        sweep_ok_short = (not p.use_sweep) or (last_sw_high is not None and i - last_sw_high <= p.sweep_lookback)

        b = bias[i]
        hd, hc, ha = h1_dir[i], h1_choch[i], h1_age[i]
        fresh = (not math.isnan(ha)) and ha <= p.htf_break_max
        type_ok = (not p.choch_only) or (hc == 1.0)
        bias_ok_bull = (not p.use_bias) or b == 1
        bias_ok_bear = (not p.use_bias) or b == -1
        st_ok_bull = (not p.use_struct) or (hd == 1 and fresh and type_ok)
        st_ok_bear = (not p.use_struct) or (hd == -1 and fresh and type_ok)
        bull_ok = bias_ok_bull and st_ok_bull
        bear_ok = bias_ok_bear and st_ok_bear

        rw = rng_hi[i] - rng_lo[i]
        in_range = p.use_range and rw > 0 and rw <= a * p.range_max_atr

        # ---- 5) nuevos setups (impulso FVG + OB)
        if i >= 2 and i - p.ob_lookback - 1 >= 0:
            raw_bull = l[i] > h[i - 2] and c[i - 1] > o[i - 1] and (l[i] - h[i - 2]) > a * p.fvg_min_atr
            raw_bear = h[i] < l[i - 2] and c[i - 1] < o[i - 1] and (l[i - 2] - h[i]) > a * p.fvg_min_atr
            if raw_bull or raw_bear:
                disp_ok = body[i - 1] > a * p.disp_mult
                vol_ok = (not p.use_vol) or v[i - 1] > vol_ma[i - 1] * p.vol_mult

                def find_ob(bull):
                    for off in range(2, p.ob_lookback + 2):
                        opp = c[i - off] < o[i - off] if bull else c[i - off] > o[i - off]
                        if opp:
                            return off
                    return None

                if p.enable_ob and raw_bull and disp_ok and vol_ok:
                    off = find_ob(True)
                    if off is not None:
                        zmid = (h[i - off] + l[i - off]) / 2
                        rng_ok_long = in_range and ((zmid - rng_lo[i]) / rw) <= p.range_edge
                        if (bull_ok or rng_ok_long) and sweep_ok_long:
                            ob_bar = i - off
                            if last_bull_ob is None or ob_bar != last_bull_ob:
                                last_bull_ob = ob_bar
                                if len(setups) >= 5:
                                    setups.pop(0)
                                setups.append(dict(bull=True, top=h[i - off], bot=l[i - off], born=i, rng=not bull_ok))
                if p.enable_ob and raw_bear and disp_ok and vol_ok:
                    off = find_ob(False)
                    if off is not None:
                        zmid = (h[i - off] + l[i - off]) / 2
                        rng_ok_short = in_range and ((zmid - rng_lo[i]) / rw) >= 1 - p.range_edge
                        if (bear_ok or rng_ok_short) and sweep_ok_short:
                            ob_bar = i - off
                            if last_bear_ob is None or ob_bar != last_bear_ob:
                                last_bear_ob = ob_bar
                                if len(setups) >= 5:
                                    setups.pop(0)
                                setups.append(dict(bull=False, top=h[i - off], bot=l[i - off], born=i, rng=not bear_ok))

        # ---- 6) disparo: retroceso al OB + vela de rechazo
        cooldown_ok = last_signal_bar is None or (i - last_signal_bar) >= p.cooldown_bars
        long_sig = short_sig = False
        sig_entry = sig_sl = sig_tp = NAN
        sig_tag = ""
        for k in range(len(setups) - 1, -1, -1):
            s = setups[k]
            drop = False
            if i - s["born"] > p.setup_expire:
                drop = True
            elif (s["bull"] and c[i] < s["bot"]) or ((not s["bull"]) and c[i] > s["top"]):
                drop = True
            elif i > s["born"] and cooldown_ok and not long_sig and not short_sig:
                mid = (s["top"] + s["bot"]) / 2
                if s["bull"]:
                    rejection = l[i] <= s["top"] and c[i] > o[i] and c[i] > mid
                    if rejection and (s["rng"] or bull_ok):
                        sl_lvl = s["bot"] - a * (p.sl_buffer_rng_atr if s["rng"] else p.sl_buffer_atr)
                        sl_dist = c[i] - sl_lvl
                        dist_ok = sl_dist > 0 and a * p.sl_min_atr <= sl_dist <= a * p.sl_max_atr
                        if s["rng"]:
                            tp = (rng_hi[i] + rng_lo[i]) / 2 if p.range_tp_mode == "mid" else rng_hi[i]
                            need = p.range_min_rr
                        elif p.tp_mode == "fixed":
                            tp = c[i] + sl_dist * p.fixed_rr
                            need = 0.0
                        else:
                            above = [x for x in liq_h if x > c[i]]
                            tp = min(above) if above else NAN
                            need = p.min_rr
                        rr_ok = (not math.isnan(tp)) and sl_dist > 0 and (tp - c[i] - cp) / (sl_dist + cp) >= need
                        if dist_ok and rr_ok:
                            long_sig = True
                            sig_entry, sig_sl, sig_tp = c[i], sl_lvl, tp
                            sig_tag = "rngOB" if s["rng"] else "trendOB"
                            drop = True
                else:
                    rejection = h[i] >= s["bot"] and c[i] < o[i] and c[i] < mid
                    if rejection and (s["rng"] or bear_ok):
                        sl_lvl = s["top"] + a * (p.sl_buffer_rng_atr if s["rng"] else p.sl_buffer_atr)
                        sl_dist = sl_lvl - c[i]
                        dist_ok = sl_dist > 0 and a * p.sl_min_atr <= sl_dist <= a * p.sl_max_atr
                        if s["rng"]:
                            tp = (rng_hi[i] + rng_lo[i]) / 2 if p.range_tp_mode == "mid" else rng_lo[i]
                            need = p.range_min_rr
                        elif p.tp_mode == "fixed":
                            tp = c[i] - sl_dist * p.fixed_rr
                            need = 0.0
                        else:
                            below = [x for x in liq_l if x < c[i]]
                            tp = max(below) if below else NAN
                            need = p.min_rr
                        rr_ok = (not math.isnan(tp)) and sl_dist > 0 and (c[i] - tp - cp) / (sl_dist + cp) >= need
                        if dist_ok and rr_ok:
                            short_sig = True
                            sig_entry, sig_sl, sig_tp = c[i], sl_lvl, tp
                            sig_tag = "rngOB" if s["rng"] else "trendOB"
                            drop = True
            if drop:
                setups.pop(k)

        # ---- 7) modo rango: barrido
        if cooldown_ok and not long_sig and not short_sig and p.use_range and p.use_sw and i >= p.range_len + 1:
            prev_hi = h[i - p.range_len:i].max()
            prev_lo = l[i - p.range_len:i].min()
            pw = prev_hi - prev_lo
            if pw > 0 and pw <= a * p.range_max_atr:
                cpos = (c[i] - l[i]) / (h[i] - l[i]) if h[i] > l[i] else 0.5
                sw_vol_ok = v[i] > vol_ma[i] * p.vol_mult
                tol = a * p.sw_tol_atr
                mid_rng = (prev_hi + prev_lo) / 2
                if not math.isnan(last_pl):
                    raw = (last_pl >= prev_lo and l[i] < last_pl and c[i] > last_pl
                           and (last_pl - l[i]) >= a * p.sw_min_wick and ((last_pl - prev_lo) / pw) <= p.range_edge)
                    if raw and p.sw_allow_long:
                        seg = pl[i - p.range_len:i]
                        touches = int(np.sum(np.abs(seg[~np.isnan(seg)] - last_pl) <= tol))
                        against = p.use_bias and b == -1
                        sl_ = l[i] - a * p.sl_buffer_rng_atr
                        dist = c[i] - sl_
                        tp_ = mid_rng if p.range_tp_mode == "mid" else prev_hi
                        vol_pass = (not (p.sw_use_vol or (p.sw_counter_strict and against))) or sw_vol_ok
                        close_pass = (not (p.sw_strong_close or (p.sw_counter_strict and against))) or cpos >= 0.66
                        dist_ok = dist > 0 and a * p.sl_min_atr <= dist <= a * p.sl_max_atr
                        rr_ok = dist > 0 and (tp_ - c[i] - cp) / (dist + cp) >= p.range_min_rr
                        if touches >= p.sw_touches and vol_pass and close_pass and dist_ok and rr_ok:
                            long_sig = True
                            sig_entry, sig_sl, sig_tp, sig_tag = c[i], sl_, tp_, "sweep"
                if not long_sig and not math.isnan(last_ph):
                    raw = (last_ph <= prev_hi and h[i] > last_ph and c[i] < last_ph
                           and (h[i] - last_ph) >= a * p.sw_min_wick and ((prev_hi - last_ph) / pw) <= p.range_edge)
                    if raw and p.sw_allow_short:
                        seg = ph[i - p.range_len:i]
                        touches = int(np.sum(np.abs(seg[~np.isnan(seg)] - last_ph) <= tol))
                        against = p.use_bias and b == 1
                        sl_ = h[i] + a * p.sl_buffer_rng_atr
                        dist = sl_ - c[i]
                        tp_ = mid_rng if p.range_tp_mode == "mid" else prev_lo
                        vol_pass = (not (p.sw_use_vol or (p.sw_counter_strict and against))) or sw_vol_ok
                        close_pass = (not (p.sw_strong_close or (p.sw_counter_strict and against))) or cpos <= 0.34
                        dist_ok = dist > 0 and a * p.sl_min_atr <= dist <= a * p.sl_max_atr
                        rr_ok = dist > 0 and (c[i] - tp_ - cp) / (dist + cp) >= p.range_min_rr
                        if touches >= p.sw_touches and vol_pass and close_pass and dist_ok and rr_ok:
                            short_sig = True
                            sig_entry, sig_sl, sig_tp, sig_tag = c[i], sl_, tp_, "sweep"

        # ---- 8) limites diarios + ejecucion
        if long_sig or short_sig:
            eq_now = mtm(i)
            day_loss_ok = (not p.use_daily_loss) or (eq_now - day_eq0) / day_eq0 * 100 > -p.max_daily_loss_pct
            day_ok = trades_today < p.max_trades_day and day_loss_ok
            if pos is None and pending is None and day_ok:
                d = abs(sig_entry - sig_sl)
                if d > 0:
                    risk_amt = eq_now * p.risk_pct / 100
                    q = min(risk_amt / d, eq_now * p.max_leverage / sig_entry)
                    if q > 0:
                        pending = dict(side="L" if long_sig else "S", qty=q, sl=sig_sl, tp=sig_tp, tag=sig_tag,
                                       risk_amt=risk_amt)
                        trades_today += 1
                        last_signal_bar = i

    if pos is not None:  # posicion abierta al final: se marca a mercado
        close_pos(n - 1, c[n - 1], "open_end")
    return pd.DataFrame(trades)


# ----------------------------------------------------------------------------------------
# Metricas
# ----------------------------------------------------------------------------------------
def summarize(tr: pd.DataFrame, capital: float, label: str = "") -> dict:
    if tr.empty:
        return dict(label=label, trades=0)
    eq = capital + tr["pnl"].cumsum()
    peak = np.maximum.accumulate(np.r_[capital, eq.values])[1:]
    dd = ((eq.values - peak) / peak).min() * 100
    wins = tr[tr["pnl"] > 0]
    loss = tr[tr["pnl"] <= 0]
    gp, gl = wins["pnl"].sum(), -loss["pnl"].sum()
    return dict(
        label=label, trades=len(tr), win_pct=round(100 * len(wins) / len(tr), 1),
        pf=round(gp / gl, 2) if gl > 0 else float("inf"),
        net_pct=round(100 * tr["pnl"].sum() / capital, 1), final=round(eq.iloc[-1], 2),
        avg_R=round(tr["r"].mean(), 3), max_dd_pct=round(dd, 1),
        long=int((tr["side"] == "L").sum()), short=int((tr["side"] == "S").sum()),
    )
