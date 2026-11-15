"""Estrategias candidatas (reglas fijas, definidas antes de ver resultados) y un control al azar.

Todas operan intradia a favor del regimen de 4h. Una senal nace al CIERRE de una vela de 15m (`created_ts` = su cierre)
y solo usa datos hasta ese cierre. Las entradas son:
  limit  -> orden limite en un nivel (EMA20 de 1h o nivel roto), valida 4 h
  market -> al precio de apertura de la vela siguiente (se llena siempre)
Stop = stop_k x ATR(1h); TP1 = 1,5 R; TP2 = 3 R (R = distancia al stop). Se cierra la mitad en cada TP.

  pullback_ema    retroceso a la EMA20 de 1h tras una extension >= 1 ATR a favor del regimen (alcista/bajista)
  nivel_roto      primera ruptura (+0,5 ATR) del maximo/minimo de 24 h previo a las ultimas 4 h; entra en el retesteo
  impulso         ruptura del maximo/minimo de 4 h con volumen relativo >= 1,5 (alcista/bajista)
  ruptura_lateral ruptura del rango de 24 h con volumen relativo >= 1,2, solo en regimen lateral (ambos sentidos)
  control         entradas al azar a favor del regimen (referencia: mismo stop y objetivos, sin criterio)
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "reporter"))
import setups  # noqa: E402

TP1_R, TP2_R = 1.5, 3.0
COOLDOWN = 16  # velas de 15m entre senales de la misma estrategia y moneda (4 h)
LIMIT_HOURS, MARKET_HOURS = 4.0, 0.25
STRATEGIES = ["pullback_ema", "nivel_roto", "impulso", "ruptura_lateral", "control"]
SEED = 12345


def _cooldown(items: list) -> list:
    items = sorted(items, key=lambda x: x[0])
    last, out = -10 ** 9, []
    for it in items:
        if it[0] - last >= COOLDOWN:
            out.append(it)
            last = it[0]
    return out


def _idx(mask) -> np.ndarray:
    return np.flatnonzero(np.asarray(mask, dtype=bool))


def generate(d, coin: str, stop_k: float = 1.5) -> list[dict]:
    """Devuelve las senales de todas las estrategias para una moneda (d = research.features.build)."""
    n = len(d)
    col = lambda c: d[c].to_numpy(dtype=float)  # noqa: E731
    close, opn, atr, ema = col("close"), col("open"), col("atr1h"), col("ema1h")
    rel = col("rel_vol")
    reg = d["regime"].to_numpy(dtype=object)
    up, dn, rg = reg == "up", reg == "down", reg == "range"
    ok = ~np.isnan(atr)
    eu, ed, pu, pd_ = col("ext_up"), col("ext_dn"), col("pre_up"), col("pre_dn")
    lvl_hi, lvl_lo = col("lvl_hi"), col("lvl_lo")
    cond_up, cond_dn = close > lvl_hi + 0.5 * atr, close < lvl_lo - 0.5 * atr
    first_up = cond_up & ~np.r_[False, cond_up[:-1]]
    first_dn = cond_dn & ~np.r_[False, cond_dn[:-1]]

    items: dict[str, list] = {s: [] for s in STRATEGIES}
    items["pullback_ema"] += [(i, "long", ema[i]) for i in _idx(up & ok & (eu >= 0) & (eu <= 0.25) & (pu >= 1.0))]
    items["pullback_ema"] += [(i, "short", ema[i]) for i in _idx(dn & ok & (ed >= 0) & (ed <= 0.25) & (pd_ >= 1.0))]
    items["nivel_roto"] += [(i, "long", lvl_hi[i]) for i in _idx(up & ok & first_up)]
    items["nivel_roto"] += [(i, "short", lvl_lo[i]) for i in _idx(dn & ok & first_dn)]
    items["impulso"] += [(i, "long", None) for i in _idx(up & ok & (close > col("hh16")) & (rel >= 1.5))]
    items["impulso"] += [(i, "short", None) for i in _idx(dn & ok & (close < col("ll16")) & (rel >= 1.5))]
    items["ruptura_lateral"] += [(i, "long", None) for i in _idx(rg & ok & (close > col("box_hi")) & (rel >= 1.2))]
    items["ruptura_lateral"] += [(i, "short", None) for i in _idx(rg & ok & (close < col("box_lo")) & (rel >= 1.2))]
    u = ((np.arange(n, dtype=np.int64) * 2654435761 + SEED) % 4294967296) / 4294967296  # azar determinista por vela
    items["control"] += [(i, "long" if up[i] else "short", None) for i in _idx((up | dn) & ok & (u < 1 / 48))]

    out = []
    for name in STRATEGIES:
        for i, side, level in _cooldown(items[name]):
            if i + 1 >= n:
                continue  # sin vela siguiente no hay como llenar ni evaluar
            market = level is None
            entry = float(opn[i + 1]) if market else float(level)
            dist = stop_k * float(atr[i])
            sign = 1 if side == "long" else -1
            order = {"side": side, "entry": entry, "stop": entry - sign * dist,
                     "tp1": entry + sign * TP1_R * dist, "tp2": entry + sign * TP2_R * dist}
            try:
                setups.parse_setup(order)
            except ValueError:
                continue
            out.append({"coin": coin, "strategy": name, "regime": reg[i], "i": int(i), **order, "stop_dist": dist,
                        "created_ts": d["end"].iloc[i].strftime(setups.FMT),
                        "entry_hours": MARKET_HOURS if market else LIMIT_HOURS})
    return out
