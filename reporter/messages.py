"""Formato de los mensajes de Telegram: REPORTE TECNICO (predictor base) y REPORTE CLAUDE."""
from __future__ import annotations

TREND = {"up": "🔺 alcista", "down": "🔻 bajista", "range": "↔️ lateral"}


def fmt_price(x: float) -> str:
    if x >= 1000:
        return f"{x:,.2f}"
    if x >= 100:
        return f"{x:.2f}"
    if x >= 1:
        return f"{x:.3f}"
    return f"{x:.4f}"


def prob_dot(p: float) -> str:
    return "🟢" if p >= 0.55 else "🔴" if p <= 0.45 else "🟡"


def rsi_label(r: float) -> str:
    return "🔥 sobrecompra" if r > 70 else "🧊 sobreventa" if r < 30 else "neutro"


VOL_LEVEL = {"alto": "⚡ alto", "bajo": "😴 bajo", "normal": "〰️ normal"}


def vol_lines(v: dict | None) -> list[str]:
    """Rango esperado de 24 h (volatilidad, no direccion) y stop sugerido; vacio si no hay pronostico."""
    if not v:
        return []
    return [f"   📏 Rango esperado 24 h: ~{v['range_pct']:.1f}% ({v['lo_pct']:.1f}–{v['hi_pct']:.1f}%) · {VOL_LEVEL.get(v['level'], v['level'])}",
            f"   🧭 Stop sugerido: ~{v['stop_pct']:.1f}% del precio"]


def technical_message(symbols: list[str], blocks: dict, preds: dict, ts: str) -> str:
    """blocks[sym]['summary'] y preds[sym] vienen de report.coin_block y baseline.predict."""
    lines = [f"📊 REPORTE TÉCNICO · {ts}", "Regla simple: tendencia 4h + RSI (sin IA, sin noticias)", ""]
    for sym in symbols:
        if sym not in preds:
            lines += [f"⚠️ {sym}: sin datos en esta corrida", ""]
            continue
        s, p = blocks[sym]["summary"], preds[sym]
        arrow = "🔻" if s["chg_24h"] < 0 else "🔺"
        lines += [
            f"🪙 {sym}  {fmt_price(s['price'])}  {arrow} {s['chg_24h'] * 100:+.1f}% (24 h)",
            f"   📈 Tendencia 4h: {TREND.get(s['trend_4h'], s['trend_4h'])} · RSI {s['rsi_4h']:.0f} {rsi_label(s['rsi_4h'])}",
            *vol_lines(s.get("vol")),
            f"   🎯 Prob. de subir en 24 h: {p['prob_up'] * 100:.0f}% {prob_dot(p['prob_up'])}",
            f"   🛡️ Se invalida si cruza {fmt_price(p['invalidation'])}",
            "",
        ]
    return "\n".join(lines).rstrip()


def claude_message(rows: list[dict], ts: str) -> str:
    """rows: symbol, price, prob_up (Claude), base_prob (tecnico), invalidation, note."""
    lines = [f"🤖 REPORTE CLAUDE · {ts}", "Análisis técnico + noticias, según las reglas de reglas.md", ""]
    for r in rows:
        lines += [
            f"🪙 {r['symbol']}  {fmt_price(r['price'])}",
            f"   🤖 Claude: {r['prob_up'] * 100:.0f}% {prob_dot(r['prob_up'])}   vs   📊 Técnico: {r['base_prob'] * 100:.0f}% {prob_dot(r['base_prob'])}",
            f"   🛡️ Se invalida si cruza {fmt_price(r['invalidation'])}",
            f"   💬 {r['note']}",
        ]
        s = r.get("setup")
        if s:
            side = "SHORT 🔻" if s["side"] == "short" else "LONG 🔺"
            lines += [
                f"   📌 {side}: entrada {fmt_price(s['entry'])} · 🛑 SL {fmt_price(s['stop'])}",
                f"   ✅ TP1 {fmt_price(s['tp1'])} · TP2 {fmt_price(s['tp2'])}",
                f"   💵 {s['margin']:.0f} $ x{s['leverage']:g}: −{s['risk_usd']:.1f} $ / +{s['tp1_usd']:.1f} $ / +{s['tp2_usd']:.1f} $",
            ]
        lines.append("")
    return "\n".join(lines).rstrip()
