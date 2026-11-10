"""Predictor base sin IA (y referencias de comparacion). Claude lo reemplazara con la misma interfaz."""
from __future__ import annotations

METHOD = "baseline_v1"


def predict(summary: dict) -> dict:
    """summary: salida de report.coin_block()['summary']. Devuelve prob_up (0.05-0.95), invalidation y note."""
    p = 0.5
    p += {"up": 0.10, "down": -0.10}.get(summary["trend_4h"], 0.0)
    if summary["rsi_4h"] > 70:
        p -= 0.05
    elif summary["rsi_4h"] < 30:
        p += 0.05
    p = round(min(0.95, max(0.05, p)), 2)

    price, atr = summary["price"], summary["atr_4h"]
    if p >= 0.5:
        inv = summary["support"] if summary["support"] is not None else price - 1.5 * atr
        note = f"sesgo alcista; se invalida bajo {inv:.4f}"
    else:
        inv = summary["resistance"] if summary["resistance"] is not None else price + 1.5 * atr
        note = f"sesgo bajista; se invalida sobre {inv:.4f}"
    return {"prob_up": p, "invalidation": float(inv), "note": note}


def reference_50() -> float:
    return 0.5


def reference_momentum(chg_24h: float) -> float:
    """Referencia 'repite la direccion de las ultimas 24 h'."""
    return 0.6 if chg_24h > 0 else 0.4 if chg_24h < 0 else 0.5
