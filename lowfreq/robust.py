"""Robustez de la tendencia diaria (sin elegir nada nuevo). Uso: python lowfreq/robust.py"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from trend_lf import metrics, portfolio  # noqa: E402

COST, FUND = 0.001, 0.0003
IS = ("2020-10-01", "2022-12-31")
OOS = ("2023-01-01", "2100-01-01")


def row(label, v, lo, hi, assets=None, funding=FUND):
    port, expo, tr = portfolio(v, COST, funding if v else 0.0, lo, hi, assets)
    m = metrics(port, expo, tr)
    return dict(caso=label, **{k: m[k] for k in ("cagr_pct", "max_dd_pct", "sharpe", "calmar", "expo_pct", "trades")})


def don(n, m):
    return dict(kind="donchian", n=n, m=m, short=False)


print("=== 1) Vecindad de parametros (largos, 3 activos). IS | OOS")
rows = []
for n, m in [(10, 5), (15, 8), (20, 10), (30, 15), (40, 20), (50, 25)]:
    for nm, (lo, hi) in (("IS", IS), ("OOS", OOS)):
        rows.append(dict(periodo=nm, **row(f"{n}/{m}", don(n, m), lo, hi)))
print(pd.DataFrame(rows).to_string(index=False))

print("\n=== 2) Por activo, Donchian 20/10 (OOS) y comprar y mantener del mismo activo")
rows = []
for label, assets in (("BTC", ["BTC"]), ("ETH", ["ETH"]), ("SOL", ["SOL"]), ("BTC+ETH", ["BTC", "ETH"]), ("BTC+ETH+SOL", None)):
    rows.append(row(f"{label} 20/10", don(20, 10), *OOS, assets))
    rows.append(row(f"{label} B&H", None, *OOS, assets))
print(pd.DataFrame(rows).to_string(index=False))

print("\n=== 3) Por anio, Donchian 20/10 vs comprar y mantener (3 activos)")
rows = []
for y in range(2021, 2027):
    lo, hi = f"{y}-01-01", f"{y}-12-31"
    pv, _, _ = portfolio(don(20, 10), COST, FUND, lo, hi)
    pb, _, _ = portfolio(None, COST, 0.0, lo, hi)
    ev, eb = (1 + pv).cumprod(), (1 + pb).cumprod()
    rows.append(dict(anio=y, dias=len(pv), v2_pct=round(100 * (ev.iloc[-1] - 1), 1), v2_dd=round(100 * (ev / ev.cummax() - 1).min(), 1),
                     bh_pct=round(100 * (eb.iloc[-1] - 1), 1), bh_dd=round(100 * (eb / eb.cummax() - 1).min(), 1)))
print(pd.DataFrame(rows).to_string(index=False))
