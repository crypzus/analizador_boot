"""Guarda las predicciones hechas por Claude (modo manual) en el journal y las envia a Telegram.

  python reporter/save_claude.py preds.json [--no-send]
preds.json: {"BTCUSDT": {"prob_up": 0.45, "invalidation": 76200.0, "note": "...",
                         "setup": {"side": "short", "entry": 67200, "stop": 69000, "tp1": 62006, "tp2": 60000}}, ...}
`setup` es opcional: se registra como operacion de estudio (100 $ x5) y se evalua con velas de 15 m.
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
import setups  # noqa: E402

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
    plans = {sym: setups.parse_setup(p["setup"]) for sym, p in preds.items() if p.get("setup")}  # ValueError si es invalido
    saved, saved_setups, rows = [], [], []
    created = journal.utcnow()  # el setup se evalua con velas posteriores a este instante (sin mirar el pasado)
    for sym, p in preds.items():
        b = base[sym]
        new_pred = not j.has_prediction(b["run_id"], sym, METHOD)  # no duplicar en la misma corrida
        new_setup = sym in plans and not j.has_setup(b["run_id"], sym)
        if not (new_pred or new_setup):
            continue
        if new_pred:
            j.save_prediction(b["run_id"], sym, b["ts"], b["price"], float(p["prob_up"]), METHOD, b["ref_mom"])
            saved.append(sym)
        row = {"symbol": sym, "price": b["price"], "prob_up": float(p["prob_up"]), "base_prob": b["prob_up"],
               "invalidation": float(p["invalidation"]), "note": p["note"]}
        if new_setup:
            j.save_setup(b["run_id"], sym, created, plans[sym], setups.MARGIN, setups.LEVERAGE)
            saved_setups.append(sym)
            row["setup"] = {**plans[sym], **setups.plan_usd(plans[sym])}
        rows.append(row)
    ts = base[next(iter(base))]["ts"].replace("T", " ")[:16] + " UTC"
    sent = send(messages.claude_message(rows, ts)) if rows else False
    return {"saved": saved, "setups": saved_setups, "telegram": sent}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # consola de Windows (cp1252) y emojis
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    print(save_claude(data, send=(lambda t: False) if "--no-send" in sys.argv else notify.send))
