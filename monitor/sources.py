"""Lectores de fuentes gratuitas. Cada funcion devuelve una lista de items:
dict(source, kind, uid, published(ISO UTC o None), title, url, text).
Todo el contenido externo es TEXTO NO CONFIABLE: se limpia y se guarda, nunca se ejecuta."""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

UA = "Mozilla/5.0 (compatible; crypto-news-monitor/0.1; personal research)"
TIMEOUT = 25
_session = requests.Session()
_session.headers.update({"User-Agent": UA})

TAG_RE = re.compile(r"<[^>]+>")
CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def clean(text: str | None, limit: int = 4000) -> str:
    t = html.unescape(TAG_RE.sub(" ", text or ""))
    t = CTRL_RE.sub("", t)
    return re.sub(r"\s+", " ", t).strip()[:limit]


def iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return parsedate_to_datetime(s)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def get(url: str, **kw) -> requests.Response:
    r = _session.get(url, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r


# ----------------------------------------------------------------------------- RSS genérico
def parse_rss(content: bytes, source: str, kind: str) -> list[dict]:
    root = ET.fromstring(content)
    ns = {"content": "http://purl.org/rss/1.0/modules/content/"}
    items = []
    for it in root.iter("item"):
        title = clean(it.findtext("title"), 300)
        link = (it.findtext("link") or "").strip()
        guid = (it.findtext("guid") or link or title).strip()
        pub = parse_date(it.findtext("pubDate"))
        body = it.findtext("content:encoded", namespaces=ns) or it.findtext("description") or ""
        items.append(dict(source=source, kind=kind, uid=guid, published=iso(pub) if pub else None,
                          title=title, url=link, text=clean(body)))
    return items


def fetch_rss(source: str, url: str, kind: str) -> list[dict]:
    return parse_rss(get(url).content, source, kind)


# ----------------------------------------------------------------------------- Trump (Truth Social)
TRUMP_ID = "107780257626128497"


def parse_truth_statuses(data: list[dict]) -> list[dict]:
    items = []
    for p in data:
        text = clean(p.get("content"))
        if not text and p.get("reblog"):
            text = "[repost] " + clean(p["reblog"].get("content"))
        created = parse_date(p.get("created_at"))
        items.append(dict(source="truth_trump", kind="political", uid=str(p["id"]),
                          published=iso(created) if created else None,
                          title=text[:140] or "[media/enlace]", url=p.get("url") or "", text=text))
    return items


def fetch_truth() -> list[dict]:
    try:
        r = get(f"https://truthsocial.com/api/v1/accounts/{TRUMP_ID}/statuses",
                params={"limit": 20, "exclude_replies": "true"})
        return parse_truth_statuses(r.json())
    except Exception:
        # Plan B: espejo con RSS
        items = fetch_rss("truth_trump", "https://www.trumpstruth.org/feed", "political")
        for it in items:
            it["uid"] = "mirror:" + it["uid"]
            if not it["title"] or it["title"].lower().startswith("[no title]"):
                it["title"] = it["text"][:140] or "[media/enlace]"
        return items


# ----------------------------------------------------------------------------- anuncios de Binance
CMS_URL = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"


def parse_binance_cms(j: dict, source: str) -> list[dict]:
    items = []
    for cat in j["data"]["catalogs"]:
        for a in cat["articles"]:
            ts = datetime.fromtimestamp(a["releaseDate"] / 1000, tz=timezone.utc)
            items.append(dict(source=source, kind="exchange", uid=str(a["id"]), published=iso(ts),
                              title=clean(a["title"], 300),
                              url=f"https://www.binance.com/en/support/announcement/detail/{a['code']}",
                              text=clean(a["title"], 300)))
    return items


def fetch_binance_cms(catalog_id: int, source: str) -> list[dict]:
    r = get(CMS_URL, params={"type": 1, "catalogId": catalog_id, "pageNo": 1, "pageSize": 15})
    return parse_binance_cms(r.json(), source)


# ----------------------------------------------------------------------------- calendario macro
CAL_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


def parse_calendar(data: list[dict], countries=("USD",), impacts=("High",)) -> list[dict]:
    items = []
    for e in data:
        if e.get("country") not in countries or e.get("impact") not in impacts:
            continue
        when = parse_date(e.get("date"))
        if when is None:
            continue
        title = f"{e['country']} {clean(e.get('title'), 120)}"
        items.append(dict(source="macro_calendar", kind="macro_event", uid=f"{title}|{e['date']}",
                          published=iso(when), title=title, url="",
                          text=f"forecast={e.get('forecast') or '-'} previous={e.get('previous') or '-'}"))
    return items


def fetch_calendar(countries=("USD",), impacts=("High",)) -> list[dict]:
    return parse_calendar(get(CAL_URL).json(), countries, impacts)


# ----------------------------------------------------------------------------- datos de mercado
FAPI = "https://fapi.binance.com"


def fetch_market(symbols: list[str]) -> list[dict]:
    rows = []
    for s in symbols:
        pi = get(f"{FAPI}/fapi/v1/premiumIndex", params={"symbol": s}).json()
        oi = get(f"{FAPI}/futures/data/openInterestHist", params={"symbol": s, "period": "1h", "limit": 25}).json()
        ls = get(f"{FAPI}/futures/data/globalLongShortAccountRatio", params={"symbol": s, "period": "1h", "limit": 168}).json()
        ls_vals = [float(x["longShortRatio"]) for x in ls]
        # percentil del ratio actual frente a sus ultimos 7 dias (cada moneda tiene su "normal")
        ls_pct = sum(v <= ls_vals[-1] for v in ls_vals) / len(ls_vals) if ls_vals else None
        kl = get(f"{FAPI}/fapi/v1/klines", params={"symbol": s, "interval": "15m", "limit": 5}).json()
        t24 = get(f"{FAPI}/fapi/v1/ticker/24hr", params={"symbol": s}).json()
        mark = float(pi["markPrice"])
        oi_now, oi_prev, oi_old = (float(oi[-1]["sumOpenInterestValue"]), float(oi[-2]["sumOpenInterestValue"]),
                                   float(oi[0]["sumOpenInterestValue"]))
        rows.append(dict(symbol=s, mark=mark, funding=float(pi["lastFundingRate"]),
                         oi_usd=oi_now, oi_chg_1h=oi_now / oi_prev - 1, oi_chg_24h=oi_now / oi_old - 1,
                         ls_ratio=ls_vals[-1] if ls_vals else None, ls_pct=ls_pct,
                         chg_1h=mark / float(kl[0][4]) - 1, chg_24h=float(t24["priceChangePercent"]) / 100))
    return rows


def fetch_fear_greed() -> dict:
    j = get("https://api.alternative.me/fng/", params={"limit": 1}).json()["data"][0]
    return dict(value=int(j["value"]), label=j["value_classification"])
