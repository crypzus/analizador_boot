"""Reglas de relevancia, prioridad, duplicados y alertas de mercado. Sin IA: palabras clave transparentes."""
from __future__ import annotations

import re

# (regex, peso, etiqueta, ignorar_mayusculas)
PATTERNS = [
    # criticas
    (r"\b(hack(ed|s)?|exploit(ed|s)?|delist(ing|ed|s)?|ban(ned|s)?|lawsuit|sues?|sued|bankrupt(cy)?|insolven\w+|"
     r"crash(ed|es)?|liquidat\w+|emergency|halt(ed)?|suspend(ed|s)?|depeg\w*)\b", 4, "critico", True),
    (r"\b(security incident|stolen|breach(ed)?|compromis\w+|withdrawals? (suspended|halted|paused|reopen\w*)|"
     r"recovery update|proof of reserves)\b", 5, "incidente", True),
    # mueven mercado
    (r"\b(ETF|stablecoins?|tariffs?|rate (cut|hike)s?|inflation|CPI|FOMC|regulat\w+|approv(al|es|ed)|"
     r"stock market|recession|sanctions?)\b", 3, "mercado", True),
    (r"\b(SEC|Fed|Federal Reserve)\b", 3, "mercado", False),
    # tus monedas
    (r"\b(Solana|Cardano)\b", 3, "SOL/ADA", True),
    (r"\b(SOL|ADA)\b", 3, "SOL/ADA", False),
    # grandes
    (r"\b(Bitcoin|Ethereum)\b", 2, "BTC/ETH", True),
    (r"\b(BTC|ETH)\b", 2, "BTC/ETH", False),
    # contexto
    (r"\b(crypto\w*|Binance|whales?|digital assets?|Trump)\b", 1, "contexto", True),
]
COMPILED = [(re.compile(p, re.I if ic else 0), w, tag) for p, w, tag, ic in PATTERNS]
CRYPTO_RX = re.compile(r"\b(crypto\w*|digital assets?|bitcoin|ethereum|blockchain|tokens?|ETF|stablecoins?|Solana|Cardano|BTC|ETH|SOL|ADA)\b", re.I)
MACRO_RX = re.compile(r"\b(FOMC|monetary policy|interest rates?|federal funds|rate (cut|hike)s?|inflation|balance sheet|"
                      r"discount rate|Powell|projections|quantitative)\b", re.I)
KIND_BASE = {"regulatory": 2, "central_bank": 2, "exchange": 3, "political": 1, "media": 0}
COIN_TAGS = {"SOL": r"\b(Solana|SOL)\b", "ADA": r"\b(Cardano|ADA)\b", "BTC": r"\b(Bitcoin|BTC)\b", "ETH": r"\b(Ethereum|ETH)\b"}


def classify(kind: str, title: str, text: str = "") -> dict:
    """Devuelve score, prioridad (HIGH/MEDIUM/LOW), etiquetas y monedas mencionadas."""
    body = f"{title} {text[:800]}"
    score, tags = KIND_BASE.get(kind, 0), set()
    matched_weights = {}
    for rx, w, tag in COMPILED:
        if rx.search(body):
            matched_weights[(rx.pattern, tag)] = w
            tags.add(tag)
    score += sum(matched_weights.values())
    coins = [c for c, p in COIN_TAGS.items() if re.search(p, body)]
    if kind == "exchange" and re.search(r"\b(delist\w*|Will List|Will Add|Adds?)\b", title, re.I):
        score += 3
    # Un comunicado de la SEC sin relacion con cripto no debe pasar de LOW: que diga "SEC" no aporta informacion
    if kind == "regulatory" and not CRYPTO_RX.search(body):
        score = min(score, 4)
    # La Fed publica muchos tramites rutinarios de bancos: solo cuenta lo que toca politica monetaria / tipos / inflacion
    if kind == "central_bank" and not MACRO_RX.search(body):
        score = min(score, 4)
    prio = "HIGH" if score >= 8 else "MEDIUM" if score >= 5 else "LOW"
    return dict(score=score, priority=prio, tags=sorted(tags), coins=coins)


# ----------------------------------------------------------------------------- duplicados entre medios
STOP = set("the and for with from that this will into over after amid says say about what have has are was were "
           "its their they them new more than your you not but can could would may via per".split())


def tokens(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9$]{4,}", title.lower()) if w not in STOP}


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def find_cluster(title: str, recent: list[tuple[int, str, str]], source: str = "", threshold: float = 0.45) -> int | None:
    """recent = [(cluster_id, title, source)]. Devuelve el cluster mas parecido de OTRA fuente (o None).
    Las noticias de una misma fuente nunca se agrupan: solo interesa saber si otro medio confirma la noticia."""
    t = tokens(title)
    if len(t) < 3:
        return None
    best, best_s = None, 0.0
    for cid, other, osrc in recent:
        if osrc == source:
            continue
        s = jaccard(t, tokens(other))
        if s >= threshold and s > best_s:
            best, best_s = cid, s
    return best


# ----------------------------------------------------------------------------- mercado
DEFAULT_THRESHOLDS = dict(funding=0.0005, oi_chg_1h=0.05, oi_chg_24h=0.15, move_1h_major=0.02, move_1h_alt=0.03,
                          ls_pct_high=0.97, ls_pct_low=0.03, fng_low=20, fng_high=80, cooldown_hours=3)
MAJORS = {"BTCUSDT", "ETHUSDT"}


def market_flags(row: dict, th: dict | None = None) -> list[tuple[str, str]]:
    """Devuelve [(clave, texto)] con condiciones inusuales para un simbolo."""
    th = {**DEFAULT_THRESHOLDS, **(th or {})}
    s, out = row["symbol"], []
    if abs(row["funding"]) >= th["funding"]:
        out.append(("funding", f"funding {row['funding'] * 100:+.3f}% por 8h (extremo)"))
    if abs(row["oi_chg_1h"]) >= th["oi_chg_1h"]:
        out.append(("oi1h", f"open interest {row['oi_chg_1h'] * 100:+.1f}% en 1h"))
    if abs(row["oi_chg_24h"]) >= th["oi_chg_24h"]:
        out.append(("oi24h", f"open interest {row['oi_chg_24h'] * 100:+.1f}% en 24h"))
    lim = th["move_1h_major"] if s in MAJORS else th["move_1h_alt"]
    if abs(row["chg_1h"]) >= lim:
        out.append(("move1h", f"movimiento {row['chg_1h'] * 100:+.1f}% en ~1h"))
    r, pct = row.get("ls_ratio"), row.get("ls_pct")
    if r is not None and pct is not None and (pct >= th["ls_pct_high"] or pct <= th["ls_pct_low"]):
        side = "longs" if pct >= th["ls_pct_high"] else "shorts"
        out.append(("ls", f"ratio long/short {r:.2f}: extremo de {side} frente a sus ultimos 7 dias (percentil {pct * 100:.0f})"))
    return out


def fng_flag(value: int, th: dict | None = None) -> str | None:
    th = {**DEFAULT_THRESHOLDS, **(th or {})}
    if value <= th["fng_low"]:
        return f"Fear & Greed {value} (miedo extremo)"
    if value >= th["fng_high"]:
        return f"Fear & Greed {value} (euforia extrema)"
    return None
