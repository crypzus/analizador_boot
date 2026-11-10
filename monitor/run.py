r"""
Monitor de noticias y mercado (SOLO LEE Y AVISA; no opera, no usa claves de trading).

Uso (desde trading-bot):
  .\.venv\Scripts\python.exe monitor\run.py --once          # un ciclo y salir
  .\.venv\Scripts\python.exe monitor\run.py                 # bucle cada 8 minutos
  .\.venv\Scripts\python.exe monitor\run.py --digest        # resumen de las ultimas 24 h en out\digest.md
Salidas: monitor\out\monitor.db (SQLite), alerts.md, events.jsonl
Opcional: TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID en variables de entorno para avisos a Telegram.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import rules  # noqa: E402
import sources as S  # noqa: E402
from store import Store  # noqa: E402

OUT = Path(__file__).parent / "out"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "ADAUSDT"]

# nombre, funcion, intervalo minimo en minutos (0 = en cada ciclo)
FEEDS = [
    ("sec", lambda: S.fetch_rss("sec", "https://www.sec.gov/news/pressreleases.rss", "regulatory"), 0),
    ("fed", lambda: S.fetch_rss("fed", "https://www.federalreserve.gov/feeds/press_all.xml", "central_bank"), 60),
    ("truth_trump", S.fetch_truth, 0),
    ("coindesk", lambda: S.fetch_rss("coindesk", "https://www.coindesk.com/arc/outboundfeeds/rss/", "media"), 0),
    ("cointelegraph", lambda: S.fetch_rss("cointelegraph", "https://cointelegraph.com/rss", "media"), 0),
    ("theblock", lambda: S.fetch_rss("theblock", "https://www.theblock.co/rss.xml", "media"), 0),
    ("decrypt", lambda: S.fetch_rss("decrypt", "https://decrypt.co/feed", "media"), 0),
    ("bitcoinmagazine", lambda: S.fetch_rss("bitcoinmagazine", "https://bitcoinmagazine.com/.rss/full/", "media"), 0),
    ("cryptoslate", lambda: S.fetch_rss("cryptoslate", "https://cryptoslate.com/feed/", "media"), 0),
    ("binance_listing", lambda: S.fetch_binance_cms(48, "binance_listing"), 0),
    ("binance_delisting", lambda: S.fetch_binance_cms(161, "binance_delisting"), 0),
    ("binance_news", lambda: S.fetch_binance_cms(49, "binance_news"), 0),
]
_last_run: dict = {}
# Fuentes que pueden rechazar clientes automaticos: si fallan, solo se registra (sin alerta WARN ni intentos de evasion)
OPTIONAL = {"fed"}


def say(msg: str) -> None:
    print(msg, flush=True)


def telegram(text: str) -> None:
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", data={"chat_id": chat, "text": text[:3900]}, timeout=15)
    except Exception as e:  # el aviso nunca debe tumbar el monitor
        say(f"  (telegram fallo: {e})")


def emit(st: Store, level: str, source: str, title: str, url: str = "", note: str = "") -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    line = f"[{level}] {source}: {title}" + (f"  -> {note}" if note else "") + (f"  {url}" if url else "")
    say(f"{ts} {line}")
    OUT.mkdir(exist_ok=True)
    with open(OUT / "alerts.md", "a", encoding="utf-8") as f:
        f.write(f"- **{ts}** `{level}` **{source}** — {title}" + (f" _({note})_" if note else "") + (f" [enlace]({url})" if url else "") + "\n")
    st.log_alert(level, source, title, url, note)
    telegram(line)


def process_items(st: Store, items: list[dict], seed: bool) -> int:
    new = 0
    for it in sorted(items, key=lambda x: x.get("published") or ""):
        key = f"{it['source']}:{it['uid']}"
        if st.has_item(key) or not it["title"]:
            continue
        cls = rules.classify(it["kind"], it["title"], it.get("text", ""))
        # Los anuncios de Binance son eventos unicos (usan texto estandar): no se agrupan
        cid = None if it["kind"] == "exchange" else rules.find_cluster(it["title"], st.recent_titles(24), it["source"])
        is_dup = cid is not None
        if cid is None:
            cid = st.next_cluster()
        st.add_item(it, cls, cid, is_dup, seed)
        new += 1
        with open(OUT / "events.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({**{k: it.get(k) for k in ("source", "kind", "published", "title", "url")}, **cls, "dup": is_dup, "seed": seed},
                               ensure_ascii=False) + "\n")
        if seed:
            continue
        if is_dup:
            n = st.cluster_size(cid)
            if n == 2 and cls["priority"] != "LOW":  # solo avisa cuando una segunda fuente lo confirma
                emit(st, cls["priority"], it["source"], it["title"], it.get("url", ""), f"confirmado por {n} fuentes")
            continue
        if cls["priority"] in ("HIGH", "MEDIUM"):
            extra = ",".join(cls["coins"] + cls["tags"][:2])
            emit(st, cls["priority"], it["source"], it["title"], it.get("url", ""), extra)
    return new


def run_feeds(st: Store, seed: bool) -> None:
    for name, fn, every in FEEDS:
        if every and time.time() - _last_run.get(name, 0) < every * 60:
            continue
        try:
            items = fn()
            _last_run[name] = time.time()
            st.health_ok(name)
            n = process_items(st, items, seed)
            say(f"  {name:18s} {len(items):3d} items, {n} nuevos")
        except Exception as e:
            fails = st.health_fail(name, f"{type(e).__name__}: {e}")
            say(f"  {name:18s} ERROR ({fails} seguidos): {type(e).__name__}: {str(e)[:120]}")
            if fails == 3 and name not in OPTIONAL:
                emit(st, "WARN", name, f"la fuente falla {fails} ciclos seguidos", note=str(e)[:100])


def run_market(st: Store) -> None:
    try:
        rows = S.fetch_market(SYMBOLS)
        st.save_market(rows)
        st.health_ok("market")
        for r in rows:
            for key, txt in rules.market_flags(r):
                k = f"mkt:{r['symbol']}:{key}"
                if not st.alerted_recently(k, rules.DEFAULT_THRESHOLDS["cooldown_hours"]):
                    st.mark_alerted(k)
                    emit(st, "MEDIUM", "mercado", f"{r['symbol']}: {txt}", note=f"precio {r['mark']:.4f}")
        say("  mercado            " + " | ".join(f"{r['symbol'][:-4]} {r['mark']:.4g} f={r['funding'] * 100:+.3f}% ls={r['ls_ratio']}" for r in rows))
    except Exception as e:
        fails = st.health_fail("market", f"{type(e).__name__}: {e}")
        say(f"  mercado            ERROR ({fails}): {e}")
    if time.time() - _last_run.get("fng", 0) >= 3600:
        try:
            v = S.fetch_fear_greed()
            _last_run["fng"] = time.time()
            st.save_fng(v)
            say(f"  fear&greed         {v['value']} ({v['label']})")
            txt = rules.fng_flag(v["value"])
            if txt and not st.alerted_recently("fng", 12):
                st.mark_alerted("fng")
                emit(st, "MEDIUM", "sentimiento", txt)
        except Exception as e:
            say(f"  fear&greed         ERROR: {e}")


def run_macro(st: Store) -> None:
    if time.time() - _last_run.get("macro", 0) >= 3600:
        try:
            events = S.fetch_calendar()
            _last_run["macro"] = time.time()
            _last_run["macro_events"] = events  # type: ignore[assignment]
            say(f"  calendario macro   {len(events)} eventos USD de alto impacto esta semana")
        except Exception as e:
            say(f"  calendario macro   ERROR: {e}")
    now = datetime.now(timezone.utc)
    for e in _last_run.get("macro_events", []):  # type: ignore[union-attr]
        when = datetime.strptime(e["published"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        mins = (when - now).total_seconds() / 60
        for limit in (60, 15):
            k = f"macro{limit}:{e['uid']}"
            if 0 < mins <= limit and not st.alerted_recently(k, 24 * 7):
                st.mark_alerted(k)
                emit(st, "HIGH", "macro", f"en ~{int(mins)} min: {e['title']}", note=e["text"])


def cycle(st: Store) -> None:
    seed = st.is_empty()
    say(f"--- ciclo {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC" + ("  (primera vez: se registran las noticias existentes sin avisar)" if seed else ""))
    run_feeds(st, seed)
    run_market(st)
    run_macro(st)


def digest(st: Store) -> None:
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    d = st.db
    lines = [f"# Resumen ultimas 24 h (generado {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC)", ""]
    lines += ["## Noticias por fuente", "| fuente | items | HIGH | MEDIUM |", "|---|---|---|---|"]
    for r in d.execute("SELECT source, COUNT(*), SUM(priority='HIGH'), SUM(priority='MEDIUM') FROM items WHERE seen>=? AND seeded=0 GROUP BY source", (since,)):
        lines.append(f"| {r[0]} | {r[1]} | {r[2] or 0} | {r[3] or 0} |")
    lines += ["", "## Alertas", ""]
    for r in d.execute("SELECT ts, level, source, title, note FROM alerts WHERE ts>=? ORDER BY ts", (since,)):
        lines.append(f"- {r[0]} `{r[1]}` **{r[2]}** — {r[3]}" + (f" _({r[4]})_" if r[4] else ""))
    lines += ["", "## Mercado (ultima lectura)", "| simbolo | precio | funding 8h | OI 1h | OI 24h | long/short | var ~1h | var 24h |", "|---|---|---|---|---|---|---|---|"]
    for r in d.execute("SELECT * FROM market WHERE ts=(SELECT MAX(ts) FROM market m2 WHERE m2.symbol=market.symbol)"):
        lines.append(f"| {r['symbol']} | {r['mark']:.4g} | {r['funding'] * 100:+.3f}% | {r['oi_chg_1h'] * 100:+.1f}% | {r['oi_chg_24h'] * 100:+.1f}% | {r['ls_ratio']} | {r['chg_1h'] * 100:+.1f}% | {r['chg_24h'] * 100:+.1f}% |")
    f = d.execute("SELECT value, label FROM fng ORDER BY ts DESC LIMIT 1").fetchone()
    if f:
        lines += ["", f"Fear & Greed: **{f[0]}** ({f[1]})"]
    OUT.mkdir(exist_ok=True)
    (OUT / "digest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    say(f"Resumen escrito en {OUT / 'digest.md'}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--digest", action="store_true")
    ap.add_argument("--interval", type=int, default=8, help="minutos entre ciclos (por defecto 8)")
    a = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    st = Store(str(OUT / "monitor.db"))
    if a.digest:
        digest(st)
        return
    while True:
        cycle(st)
        if a.once:
            break
        time.sleep(a.interval * 60 + random.uniform(-20, 20))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nMonitor detenido.")
