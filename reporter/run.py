"""Orquestador del reporter. Solo analiza y avisa; no opera ni usa claves de trading.

  python reporter/run.py --once     una corrida
  python reporter/run.py            bucle cada 4 h (Ctrl+C para parar)
  python reporter/run.py --stats    estadisticas del journal
Opcional: TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID en variables de entorno.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import baseline  # noqa: E402
import candles  # noqa: E402
import journal  # noqa: E402
import notify  # noqa: E402
import report  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent / "out"
MONITOR_DB = ROOT / "monitor" / "out" / "monitor.db"
INTERVAL_S = 4 * 3600
STALE_LOCK_S = 3600


def _take_lock(out_dir: Path) -> bool:
    lock = out_dir / "run.lock"
    if lock.exists() and time.time() - lock.stat().st_mtime > STALE_LOCK_S:
        lock.unlink()  # bloqueo viejo de una corrida que murio
    try:
        os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        return True
    except FileExistsError:
        return False


def _telegram_text(blocks: dict, preds: dict) -> str:
    lines = ["Reporte de mercado (prob. de que suba en 24 h)"]
    for sym in candles.PAIRS:
        if sym in preds:
            s, p = blocks[sym]["summary"], preds[sym]
            lines.append(f"{sym}: {s['price']:.4f} ({s['chg_24h'] * 100:+.1f}% 24 h) -> sube {p['prob_up'] * 100:.0f}% | {p['note']}")
        else:
            lines.append(f"{sym}: sin datos")
    lines.append(report.DISCLAIMER)
    return "\n".join(lines)


def _write_stats(j: journal.Journal, out_dir: Path) -> None:
    def f(x):
        return "n/d" if x is None else f"{x:.3f}"
    rows = ["# Estadisticas del journal", "", "| Moneda | Evaluadas | Brier modelo | Brier 50% | Brier momentum | Acierto | Acierto momentum |",
            "|---|---|---|---|---|---|---|"]
    g = j.stats()["global"]
    for m in j.methods() or [None]:
        st = j.stats(m)
        rows += ["", f"## Metodo: {m or 'ninguno'}", "", rows[2], rows[3]]
        for k, v in st.items():
            rows.append(f"| {k} | {v['n_resolved']} | {f(v['brier'])} | {f(v['brier_ref50'])} | {f(v['brier_mom'])} | "
                        f"{f(v['accuracy'])} | {f(v['accuracy_mom'])} |")
    rows += ["", f"Corridas completadas: {g['n_runs']} (fallidas/sin terminar: {g['n_failed_runs']}). "
                 "Se necesitan >=100 evaluadas por moneda antes de concluir nada."]
    (out_dir / "stats.md").write_text("\n".join(rows), encoding="utf-8")


def run_once(out_dir=OUT, fetcher=candles.fetch_all, monitor_db=MONITOR_DB, send=notify.send,
             resolver=candles.fetch_price_at) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not _take_lock(out_dir):
        print("Ya hay otra corrida en curso (out/run.lock). Salgo sin hacer nada.")
        return {"skipped": "lock"}
    try:
        j = journal.Journal(str(out_dir / "journal.db"))
        ts = journal.utcnow()
        run_id = j.start_run(ts)

        for p in j.pending(ts):  # evaluar predicciones con >= 24 h
            price = resolver(p["symbol"], p["ts"])
            if price is not None:
                j.resolve(p["id"], price, ts)

        blocks, frames_1h, failed = {}, {}, []
        for sym in candles.PAIRS:
            try:
                frames = fetcher(sym)
                blocks[sym] = report.coin_block(sym, frames)
                frames_1h[sym] = frames["1h"]
            except Exception as e:  # una moneda que falla no frena a las demas
                print(f"[{sym}] sin datos: {type(e).__name__}: {e}")
                blocks[sym] = None
                failed.append(sym)

        ok = [s for s in candles.PAIRS if blocks[s]]
        if not ok:
            j.end_run(run_id, False, "ninguna moneda pudo descargarse")
            _write_stats(j, out_dir)
            return {"ok": [], "failed": failed}

        preds = {}
        with (out_dir / "predictions.jsonl").open("a", encoding="utf-8") as fh:
            for sym in ok:
                s = blocks[sym]["summary"]
                p = baseline.predict(s)
                preds[sym] = p
                j.save_prediction(run_id, sym, ts, s["price"], p["prob_up"], baseline.METHOD,
                                  baseline.reference_momentum(s["chg_24h"]))
                fh.write(json.dumps({"ts": ts, "symbol": sym, "price": s["price"], **p}) + "\n")

        text = report.build_report(blocks, report.news_context(str(monitor_db)), report.btc_correlation(frames_1h))
        text += "\n\n## Predicciones (prob. de subir en 24 h)\n" + "\n".join(
            f"- {s}: {p['prob_up'] * 100:.0f}% | {p['note']}" for s, p in preds.items())
        (out_dir / "report.md").write_text(text, encoding="utf-8")

        sent = send(_telegram_text(blocks, preds))
        j.end_run(run_id, True, f"fallidas: {failed}; telegram: {sent}")
        _write_stats(j, out_dir)
        return {"ok": ok, "failed": failed, "telegram": sent}
    finally:
        (out_dir / "run.lock").unlink(missing_ok=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()
    if a.stats:
        OUT.mkdir(parents=True, exist_ok=True)
        _write_stats(journal.Journal(str(OUT / "journal.db")), OUT)
        print((OUT / "stats.md").read_text(encoding="utf-8"))
        return
    while True:
        print(run_once())
        if a.once:
            return
        time.sleep(INTERVAL_S)


if __name__ == "__main__":
    main()
