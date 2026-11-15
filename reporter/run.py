"""Orquestador del reporter. Solo analiza y avisa; no opera ni usa claves de trading.

  python reporter/run.py --once     una corrida
  python reporter/run.py            bucle cada 4 h (Ctrl+C para parar)
  python reporter/run.py --stats    estadisticas del journal
  python reporter/run.py --claude   ademas pide la prediccion a la API de Claude (apagado por defecto)
Opcional: TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID en variables de entorno.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import baseline  # noqa: E402
import candles  # noqa: E402
import claude_agent  # noqa: E402
import journal  # noqa: E402
import messages  # noqa: E402
import notify  # noqa: E402
import report  # noqa: E402
import setups  # noqa: E402
import volatility  # noqa: E402

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


def _telegram_text(blocks: dict, preds: dict, ts: str = "") -> str:
    return messages.technical_message(candles.PAIRS, blocks, preds, ts or journal.utcnow().replace("T", " ")[:16] + " UTC")


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
    ss = j.setup_stats()
    if ss["global"]["n"]:
        rows += ["", f"## Setups de estudio (papel: {setups.MARGIN:.0f} $ x{setups.LEVERAGE}; mitad en TP1, mitad en TP2)", "",
                 "| Moneda | Setups | Activos | Sin entrada | Operados | Ganadores | Stops | TP2 | P&L total $ | P&L medio $ |",
                 "|---|---|---|---|---|---|---|---|---|---|"]
        for k, v in ss.items():
            rows.append(f"| {k} | {v['n']} | {v['active']} | {v['no_fill']} | {v['traded']} | {v['wins']} | {v['stops']} | "
                        f"{v['tp2']} | {v['total']:+.2f} | {f(v['avg'])} |")
        rows.append("Los setups de varias monedas son casi la misma apuesta (correlacion ~0.9): no los cuentes como independientes.")
    vs = j.vol_stats()
    if vs["n"]:
        fx = lambda x: f"x{math.exp(x):.2f}"  # noqa: E731
        rows += ["", "## Pronostico de volatilidad (en vivo, rango de 24 h)", "",
                 "| Evaluados | Error tipico modelo | Persistencia | Media 30 d | Real dentro de la banda | Sesgo (log) |",
                 "|---|---|---|---|---|---|",
                 f"| {vs['n']} | {fx(vs['rmse_model'])} | {fx(vs['rmse_pers'])} | {fx(vs['rmse_m30'])} | "
                 f"{vs['inside'] * 100:.0f} % | {vs['bias']:+.2f} |"]
    rows += ["", f"Corridas completadas: {g['n_runs']} (fallidas/sin terminar: {g['n_failed_runs']}). "
                 "Se necesitan >=100 evaluadas por moneda antes de concluir nada."]
    (out_dir / "stats.md").write_text("\n".join(rows), encoding="utf-8")


def _evaluate_setups(j: journal.Journal, setup_fetch) -> None:
    """Re-simula cada setup sin cerrar con las velas 15m posteriores a su creacion (sin estado: se puede repetir)."""
    for st in j.unfinished_setups():
        try:
            res = setups.simulate(st, setup_fetch(st["symbol"], st["created_ts"]), st["margin"], st["leverage"])
            j.update_setup(st["id"], res)
        except Exception as e:  # noqa: BLE001  un setup que no se puede evaluar queda para la proxima corrida
            print(f"[setup {st['symbol']}] sin evaluar: {type(e).__name__}: {e}")


def _resolve_vol(j: journal.Journal, vol_resolver, now: str) -> None:
    """Evalua los pronosticos de volatilidad con >= 24 h: rango real = ln(max alto / min bajo) de esas 24 h."""
    import math
    for p in j.pending_vol(now):
        try:
            df = vol_resolver(p["symbol"], p["as_of"])
            if df is not None:
                j.resolve_vol(p["id"], math.log(df["high"].max() / df["low"].min()), now)
        except Exception as e:  # noqa: BLE001  queda pendiente para la proxima corrida
            print(f"[vol {p['symbol']}] sin evaluar: {type(e).__name__}: {e}")


def run_once(out_dir=OUT, fetcher=candles.fetch_all, monitor_db=MONITOR_DB, send=notify.send,
             resolver=candles.fetch_price_at, use_claude: bool | None = None,
             claude_ask=None, setup_fetch=candles.fetch_candles_since, vol_model="auto",
             vol_resolver=candles.fetch_hourly_after) -> dict:
    """use_claude: None -> depende de REPORTER_USE_CLAUDE=1 (apagado por defecto).
    vol_model: "auto" carga reporter/vol_model.json; None desactiva el pronostico de volatilidad."""
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
        _evaluate_setups(j, setup_fetch)
        _resolve_vol(j, vol_resolver, ts)
        model = volatility.load_model() if vol_model == "auto" else vol_model

        blocks, frames_1h, failed = {}, {}, []
        for sym in candles.PAIRS:
            try:
                frames = fetcher(sym)
                vf = volatility.forecast(frames.get("1h_long"), model, sym)
                blocks[sym] = report.coin_block(sym, frames, vf)
                frames_1h[sym] = frames["1h"]
                if vf:
                    j.save_vol_forecast(run_id, sym, vf)
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
        result = {"ok": ok, "failed": failed, "telegram": sent}
        if claude_agent.enabled() if use_claude is None else use_claude:
            result["claude"] = claude_agent.run_step(out_dir, text, ask=claude_ask or claude_agent.ask_claude, send=send)
            print(f"[claude] {result['claude']}")
        _write_stats(j, out_dir)
        return result
    finally:
        (out_dir / "run.lock").unlink(missing_ok=True)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # consola de Windows (cp1252) y emojis
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--claude", action="store_true", help="activa el paso automatico con la API de Claude")
    a = ap.parse_args()
    if a.stats:
        OUT.mkdir(parents=True, exist_ok=True)
        _write_stats(journal.Journal(str(OUT / "journal.db")), OUT)
        print((OUT / "stats.md").read_text(encoding="utf-8"))
        return
    while True:
        print(run_once(use_claude=True if a.claude else None))
        if a.once:
            return
        time.sleep(INTERVAL_S)


if __name__ == "__main__":
    main()
