"""Pruebas sin red del monitor. Ejecutar:  python monitor/test_monitor.py"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import rules  # noqa: E402
import sources as S  # noqa: E402
from store import Store  # noqa: E402

RSS = """﻿<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><title>x</title>
<item><title>   SEC Charges Crypto Firm &amp; Founder </title><link>https://x/1</link><guid>g1</guid>
<pubDate>Fri, 06 Nov 2026 05:14:48 +0000</pubDate><description>&lt;p&gt;Fraud &lt;b&gt;case&lt;/b&gt;&lt;/p&gt;</description>
<content:encoded><![CDATA[<p>Full text <script>alert(1)</script> here</p>]]></content:encoded></item>
<item><title>Second</title><link>https://x/2</link><pubDate>Thu, 05 Nov 2026 21:46:35 +0000</pubDate></item></channel></rss>""".encode("utf-8")


def test_rss():
    it = S.parse_rss(RSS, "sec", "regulatory")
    assert len(it) == 2
    assert it[0]["title"] == "SEC Charges Crypto Firm & Founder"
    assert it[0]["published"] == "2026-11-06T05:14:48Z"
    assert "<" not in it[0]["text"] and "Full text" in it[0]["text"]
    assert it[1]["uid"] == "https://x/2"  # sin guid usa el enlace
    print("OK rss (BOM, HTML limpiado, fechas)")


def test_truth_and_binance_and_calendar():
    t = S.parse_truth_statuses([{"id": 5, "created_at": "2026-11-06T00:14:11.786Z", "content": "<p>Stock Market crashed &amp; BIG tariffs</p>", "url": "u"},
                                {"id": 6, "created_at": "2026-11-05T13:22:19.607Z", "content": "", "reblog": {"content": "<p>hi</p>"}}])
    assert t[0]["published"] == "2026-11-06T00:14:11Z" and t[0]["text"] == "Stock Market crashed & BIG tariffs"
    assert t[1]["text"] == "[repost] hi"
    b = S.parse_binance_cms({"data": {"catalogs": [{"articles": [{"id": 1, "code": "abc", "title": "Binance Will List FOO (FOO)", "releaseDate": 1793274532329}]}]}}, "binance_listing")
    assert b[0]["url"].endswith("/abc") and b[0]["published"].startswith("2026-")
    c = S.parse_calendar([{"title": "CPI m/m", "country": "USD", "date": "2026-11-12T08:30:00-05:00", "impact": "High", "forecast": "0.3%", "previous": "0.2%"},
                          {"title": "x", "country": "EUR", "date": "2026-11-12T08:30:00-05:00", "impact": "High"},
                          {"title": "y", "country": "USD", "date": "2026-11-12T08:30:00-05:00", "impact": "Low"}])
    assert len(c) == 1 and c[0]["published"] == "2026-11-12T13:30:00Z"
    print("OK truth, binance cms, calendario (zona horaria -> UTC)")


def test_classify():
    p = lambda k, t: rules.classify(k, t)  # noqa: E731
    assert p("exchange", "Binance Will Delist ABC, XYZ on 2026-11-20")["priority"] == "HIGH"
    assert p("exchange", "Binance Will List Solana (SOL) pairs")["priority"] == "HIGH"
    assert p("exchange", "Binance Will List FOO with Seed Tag")["priority"] in ("MEDIUM", "HIGH")
    assert p("exchange", "Security Incident | Recovery Update #9: Withdrawals for More Assets to Reopen (2026-11-06)")["priority"] == "HIGH"
    assert p("exchange", "Binance Futures Updates Trading Fee Discount for TradFi Perps (2026-11-04)")["priority"] == "LOW"
    assert p("political", "The Stock Market crashed today because of tariffs")["priority"] == "HIGH"
    assert p("political", "Happy birthday to a great American")["priority"] == "LOW"
    assert p("regulatory", "SEC Adopts Amendments to Update Definitions of Small Entity")["priority"] == "LOW"  # sin relacion con cripto
    assert p("regulatory", "SEC Charges Crypto Firm and Founder in Fraud")["priority"] == "MEDIUM"
    assert p("regulatory", "SEC approves spot Solana ETF")["priority"] == "HIGH"
    assert p("central_bank", "Federal Reserve issues FOMC statement")["priority"] == "HIGH"
    assert p("central_bank", "Federal Reserve Board announces approval of application by The Bank of Nova Scotia")["priority"] == "LOW"
    assert p("media", "Zcash upgrade goes live, speeding up private payments")["priority"] == "LOW"
    assert p("media", "Solana ETF approved by SEC after hack fears")["priority"] == "HIGH"
    c = p("media", "Cardano (ADA) jumps as Bitcoin rises")
    assert set(c["coins"]) == {"ADA", "BTC"}
    # tickers sensibles a mayusculas: la palabra 'ada' en minusculas no cuenta
    assert "ADA" not in p("media", "Mr. ada lovelace biography")["coins"]
    print("OK clasificacion y prioridades")


def test_cluster():
    recent = [(1, "Bitcoin Price Trades Near $76,000 as Nasdaq Tumbles into Correction", "coindesk")]
    assert rules.find_cluster("Bitcoin price trades near $76,000 as Nasdaq tumbles into a correction", recent, "theblock") == 1
    assert rules.find_cluster("Bitcoin price trades near $76,000 as Nasdaq tumbles into a correction", recent, "coindesk") is None  # misma fuente
    assert rules.find_cluster("Zcash NU7 upgrade goes live", recent, "theblock") is None
    assert rules.find_cluster("ETF", recent, "theblock") is None  # titulo demasiado corto
    print("OK duplicados entre medios")


def test_market_flags():
    base = dict(symbol="SOLUSDT", funding=0.0001, oi_chg_1h=0.01, oi_chg_24h=0.02, chg_1h=0.005, ls_ratio=3.7, ls_pct=0.6)
    assert rules.market_flags(base) == []  # un ratio alto pero normal para esa moneda no alerta
    f = rules.market_flags({**base, "funding": 0.0008, "chg_1h": -0.04, "ls_pct": 0.99, "oi_chg_1h": 0.06})
    assert {k for k, _ in f} == {"funding", "move1h", "ls", "oi1h"}
    assert rules.market_flags({**base, "symbol": "BTCUSDT", "chg_1h": 0.025})[0][0] == "move1h"  # umbral menor en BTC
    assert rules.fng_flag(15) and rules.fng_flag(50) is None and rules.fng_flag(85)
    print("OK alertas de mercado")


def test_store():
    d = tempfile.mkdtemp()
    st = Store(str(Path(d) / "t.db"))
    assert st.is_empty()
    it = dict(source="x", kind="media", uid="u1", published="2026-11-06T00:00:00Z", title="Hello world title", url="", text="")
    cls = rules.classify("media", it["title"])
    st.add_item(it, cls, 1, False, False)
    st.add_item(it, cls, 2, False, False)  # misma clave: se ignora
    assert st.has_item("x:u1") and not st.is_empty()
    assert st.db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1
    assert not st.alerted_recently("k", 1)
    st.mark_alerted("k")
    assert st.alerted_recently("k", 1)
    assert st.health_fail("s", "e") == 1 and st.health_fail("s", "e") == 2
    st.health_ok("s")
    assert st.db.execute("SELECT fails FROM health WHERE source='s'").fetchone()[0] == 0
    print("OK almacenamiento (dedupe, cooldown, salud)")


if __name__ == "__main__":
    test_rss()
    test_truth_and_binance_and_calendar()
    test_classify()
    test_cluster()
    test_market_flags()
    test_store()
    print("\nTODAS LAS PRUEBAS PASARON")
