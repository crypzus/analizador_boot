"""Guarda las predicciones hechas por Claude (modo manual) en el journal y las envia a Telegram.

  python reporter/save_claude.py preds.json [--no-send]
preds.json: {"BTCUSDT": {"prob_up": 0.45, "invalidation": 76200.0, "note": "..."}, ...}
Cuelga las predicciones de la ultima corrida (mismo precio y hora que la del predictor base) con metodo `claude_v1`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import baseline  # noqa: E402
import journal  # noqa: E402
import messages  # noqa: E402
import notify  # noqa: E402

OUT = Path(__file__).resolve().parent / "out"
METHOD = "claude_v1"


def save_claude(preds: dict, out_dir=OUT, send=notify.send) -> dict:
    j = journal.Journal(str(Path(out_dir) / "journal.db"))
    base = {r["symbol"]: r for r in j.last_run_predictions(baseline.METHOD)}
    if not base:
        raise ValueError("no hay una corrida previa: ejecuta run.py --once primero")
    for sym, p in preds.items():
        if sym not in base:
            raise ValueError(f"{sym} no tiene datos en la ultima corrida")
        if not 0.05 <= float(p["prob_up"]) <= 0.95:
            raise ValueError(f"{sym}: prob_up fuera de 0.05-0.95")
    saved, rows = [], []
    for sym, p in preds.items():
        b = base[sym]
        if j.has_prediction(b["run_id"], sym, METHOD):
            continue  # no duplicar en la misma corrida
        j.save_prediction(b["run_id"], sym, b["ts"], b["price"], float(p["prob_up"]), METHOD, b["ref_mom"])
        saved.append(sym)
        rows.append({"symbol": sym, "price": b["price"], "prob_up": float(p["prob_up"]), "base_prob": b["prob_up"],
                     "invalidation": float(p["invalidation"]), "note": p["note"]})
    ts = base[next(iter(base))]["ts"].replace("T", " ")[:16] + " UTC"
    sent = send(messages.claude_message(rows, ts)) if saved else False
    return {"saved": saved, "telegram": sent}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    print(save_claude(data, send=(lambda t: False) if "--no-send" in sys.argv else notify.send))
