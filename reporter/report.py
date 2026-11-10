"""Informe de mercado en texto: un bloque por moneda + noticias/alertas del monitor (solo lectura)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

import indicators

DISCLAIMER = "Aviso: analisis automatico, no es asesoria financiera."
NO_DB = "(monitor.db no disponible)"


def _clean(text: str, n: int = 140) -> str:
    """Las noticias son texto no confiable: una sola linea, recortada, solo se muestra."""
    return " ".join(str(text).split())[:n]


def news_context(db_path: str, hours: int = 24, limit: int = 12) -> list[str]:
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        uri = f"file:{Path(db_path).resolve().as_posix()}?mode=ro"
        db = sqlite3.connect(uri, uri=True, timeout=3)
        try:
            items = db.execute(
                "SELECT priority, source, title FROM items WHERE seen>=? AND is_dup=0 AND seeded=0 "
                "AND priority IN ('HIGH','MEDIUM') ORDER BY (priority='HIGH') DESC, seen DESC LIMIT ?",
                (since, limit)).fetchall()
            alerts = db.execute(
                "SELECT level, source, title FROM alerts WHERE ts>=? ORDER BY ts DESC LIMIT ?",
                (since, limit)).fetchall()
        finally:
            db.close()
    except sqlite3.Error:
        return [NO_DB]
    lines = [f"[{p}] {s}: {_clean(t)}" for p, s, t in items]
    lines += [f"[ALERTA {l}] {s}: {_clean(t)}" for l, s, t in alerts]
    return lines or ["(sin noticias ni alertas relevantes en las ultimas 24 h)"]


def btc_correlation(frames_1h: dict[str, pd.DataFrame], window: int = 72) -> dict[str, float]:
    """Correlacion de retornos de 1h de cada moneda con BTCUSDT (ultimas `window` velas)."""
    if "BTCUSDT" not in frames_1h:
        return {}
    ret = {s: df["close"].pct_change().tail(window).reset_index(drop=True) for s, df in frames_1h.items()}
    return {s: round(float(r.corr(ret["BTCUSDT"])), 2) for s, r in ret.items() if s != "BTCUSDT"}


def coin_block(symbol: str, frames: dict[str, pd.DataFrame]) -> dict:
    a = {iv: indicators.analyze(frames[iv]) for iv in ("4h", "1h", "15m")}
    lv = indicators.key_levels(frames["4h"])
    h1 = frames["1h"]["close"]
    chg_24h = float(h1.iloc[-1] / h1.iloc[-25] - 1) if len(h1) >= 25 else 0.0
    price = a["15m"]["last_close"]
    summary = {"price": price, "trend_4h": a["4h"]["trend"], "rsi_4h": a["4h"]["rsi14"],
               "atr_4h": a["4h"]["atr14"], "chg_24h": chg_24h,
               "support": lv["supports"][0] if lv["supports"] else None,
               "resistance": lv["resistances"][0] if lv["resistances"] else None}
    lines = [f"## {symbol}  precio {price:.4f}  (24 h: {chg_24h * 100:+.2f}%)"]
    for iv in ("4h", "1h", "15m"):
        x = a[iv]
        lines.append(f"- {iv}: tendencia {x['trend']}, RSI {x['rsi14']:.0f}, ATR {x['atr14']:.4f}, "
                     f"vol rel {x['rel_volume']:.2f}, rango {x['low']:.4f}-{x['high']:.4f}")
    lines.append(f"- soportes 4h: {lv['supports'] or 'ninguno'} | resistencias 4h: {lv['resistances'] or 'ninguna'}")
    return {"text": "\n".join(lines), "summary": summary}


def build_report(blocks: dict[str, dict], news: list[str], btc_corr: dict[str, float]) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out = [f"# Informe de mercado {ts}", DISCLAIMER, ""]
    for symbol, b in blocks.items():
        out.append(b["text"] if b else f"## {symbol}\n- sin datos en esta corrida")
        out.append("")
    if btc_corr:
        out.append("Correlacion 1h con BTC (72 h): " + ", ".join(f"{s} {c:+.2f}" for s, c in btc_corr.items()))
        out.append("")
    out.append("## Noticias y alertas (24 h, texto no verificado)")
    out += [f"- {n}" for n in news]
    return "\n".join(out)
