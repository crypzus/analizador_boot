"""Setups de operacion de estudio (en papel, sin dinero real): validacion, simulacion con velas de 15 m y P&L.

Reglas de la simulacion (conservadoras, para no inflar resultados):
- La entrada es una orden limite al precio `entry`; se llena si una vela lo toca dentro de 24 h desde la creacion.
- Si en una misma vela se tocan el stop y un objetivo, gana el stop. En la vela de entrada solo cuenta el stop.
- Se cierra la mitad de la posicion en TP1 y la otra mitad en TP2; el stop original sigue vigente para el resto.
- Si pasan 72 h desde la entrada sin cerrar, se cierra el resto al cierre de la ultima vela dentro de ese plazo.
- Comision de 0,05 % por lado sobre el nocional. No incluye funding.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

FMT = "%Y-%m-%dT%H:%M:%SZ"
MARGIN, LEVERAGE, FEE = 100.0, 5, 0.0005
ENTRY_HOURS, HOLD_HOURS = 24, 72
STEP = pd.Timedelta(minutes=15)


def parse_setup(d) -> dict:
    """Valida un setup {side, entry, stop, tp1, tp2}. Lanza ValueError si falta algo o no es coherente."""
    try:
        s = {"side": str(d["side"]).lower(), "entry": float(d["entry"]), "stop": float(d["stop"]),
             "tp1": float(d["tp1"]), "tp2": float(d["tp2"])}
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"setup invalido: {e}") from e
    if s["side"] == "short":
        ok = 0 < s["tp2"] < s["tp1"] < s["entry"] < s["stop"]
    elif s["side"] == "long":
        ok = 0 < s["stop"] < s["entry"] < s["tp1"] < s["tp2"]
    else:
        raise ValueError("setup: side debe ser 'long' o 'short'")
    if not ok:
        raise ValueError(f"setup incoherente para {s['side']}: se espera stop/entrada/TP1/TP2 en orden")
    return s


def _leg(s: dict, fraction: float, price: float, notional: float) -> float:
    move = (s["entry"] - price) if s["side"] == "short" else (price - s["entry"])
    return notional * fraction * move / s["entry"]


def plan_usd(s: dict, margin: float = MARGIN, leverage: float = LEVERAGE) -> dict:
    """Cifras para mostrar (posicion completa, sin comisiones): perdida en el stop y ganancia en cada objetivo."""
    n = margin * leverage
    return {"margin": margin, "leverage": leverage, "risk_usd": -_leg(s, 1, s["stop"], n),
            "tp1_usd": _leg(s, 1, s["tp1"], n), "tp2_usd": _leg(s, 1, s["tp2"], n)}


def _ts(t) -> str:
    return pd.Timestamp(t).strftime(FMT)


def simulate(s: dict, df: pd.DataFrame, margin: float = MARGIN, leverage: float = LEVERAGE,
             fee: float = FEE) -> dict:
    """Recorre las velas 15m CERRADAS posteriores a `created_ts`. Devuelve
    {status: waiting|open|closed, outcome, filled_ts, closed_ts, pnl}. Sin estado: se puede repetir en cada corrida."""
    notional = margin * leverage
    short = s["side"] == "short"
    created = pd.Timestamp(datetime.strptime(s["created_ts"], FMT).replace(tzinfo=timezone.utc))
    entry_deadline = created + timedelta(hours=ENTRY_HOURS)

    def hit_stop(c) -> bool:
        return c.high >= s["stop"] if short else c.low <= s["stop"]

    def hit(c, level: float) -> bool:
        return c.low <= level if short else c.high >= level

    gross, closed_frac, remaining, tp1_hit = 0.0, 0.0, 1.0, False
    filled_at, last_end, prev_close = None, None, None

    def done(outcome: str, when, price: float | None = None) -> dict:
        nonlocal gross, closed_frac, remaining
        if price is not None and remaining > 0:
            gross += _leg(s, remaining, price, notional)
            closed_frac += remaining
            remaining = 0.0
        pnl = gross - notional * fee * (1 + closed_frac) if outcome != "no_fill" else 0.0
        return {"status": "closed", "outcome": outcome, "filled_ts": _ts(filled_at) if filled_at is not None else None,
                "closed_ts": _ts(when), "pnl": round(pnl, 4)}

    for c in df.itertuples():
        t = pd.Timestamp(c.open_time)
        last_end = t + STEP
        if filled_at is None:
            if t >= entry_deadline:
                return done("no_fill", entry_deadline)
            if not ((c.high >= s["entry"]) if short else (c.low <= s["entry"])):
                continue
            filled_at = t
            if hit_stop(c):
                return done("stop", t, s["stop"])
            prev_close = c.close
            continue
        if t >= filled_at + timedelta(hours=HOLD_HOURS):
            return done("tp1_timeout" if tp1_hit else "timeout", t, prev_close)
        if hit_stop(c):
            return done("tp1_stop" if tp1_hit else "stop", t, s["stop"])
        if not tp1_hit and hit(c, s["tp1"]):
            gross += _leg(s, 0.5, s["tp1"], notional)
            closed_frac += 0.5
            remaining, tp1_hit = 0.5, True
        if tp1_hit and hit(c, s["tp2"]):
            return done("tp2", t, s["tp2"])
        prev_close = c.close

    if filled_at is None:
        if last_end is not None and last_end >= entry_deadline:
            return done("no_fill", entry_deadline)
        return {"status": "waiting", "outcome": None, "filled_ts": None, "closed_ts": None, "pnl": None}
    if last_end >= filled_at + timedelta(hours=HOLD_HOURS):
        return done("tp1_timeout" if tp1_hit else "timeout", last_end, prev_close)
    return {"status": "open", "outcome": None, "filled_ts": _ts(filled_at), "closed_ts": None, "pnl": None}
