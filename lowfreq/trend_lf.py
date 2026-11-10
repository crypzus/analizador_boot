"""
Tendencia de baja frecuencia (diario) en BTC, ETH, SOL (futuros perpetuos Binance), sin apalancamiento.

Protocolo (fijado ANTES de ver resultados):
  - Variantes cerradas V0..V4 (abajo). Peso fijo 1/3 por activo, 1x, sin apalancamiento.
  - Senal al cierre del dia t-1; se opera en la APERTURA del dia t. Retorno abierto-a-abierto.
  - Costes por cambio de posicion: comision 0.05% + slippage 0.05% por lado. Funding supuesto:
    0.03%/dia (0.01% por 8h) pagado por largos y cobrado por cortos.
  - ETAPA IS (seleccion): 2020-10-01..2022-12-31.
    Criterio: Sharpe > 0.8, CAGR > 0 y max drawdown MENOR (menos severo) que el de comprar y mantener la cesta.
  - ETAPA OOS (validacion, una sola vez): 2023-01-01..hoy, solo para las que pasaron IS.
Uso:  python lowfreq/trend_lf.py is | oos [V0 V1 ...]   (--funding 0.0003 --cost 0.001)
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(__file__).parent.parent / "user_data" / "data" / "binance" / "futures"
ASSETS = ["BTC", "ETH", "SOL"]

VARIANTS = {
    "V0": dict(kind="donchian", n=50, m=25, short=False),
    "V1": dict(kind="donchian", n=50, m=25, short=True),
    "V2": dict(kind="donchian", n=20, m=10, short=False),
    "V3": dict(kind="donchian", n=100, m=50, short=False),
    "V4": dict(kind="ema", n=100, short=False),
}


def load(a: str) -> pd.DataFrame:
    d = pd.read_feather(DATA / f"{a}_USDT_USDT-1d-futures.feather")
    d["date"] = pd.to_datetime(d["date"], utc=True)
    return d.set_index("date")[["open", "high", "low", "close"]]


def signals(df: pd.DataFrame, v: dict) -> np.ndarray:
    c = df["close"]
    n = len(df)
    pos = np.zeros(n)
    if v["kind"] == "ema":
        ema = c.ewm(span=v["n"], adjust=False).mean()
        return np.where(c.values > ema.values, 1.0, 0.0)
    hh_n = c.shift(1).rolling(v["n"]).max().values
    ll_n = c.shift(1).rolling(v["n"]).min().values
    hh_m = c.shift(1).rolling(v["m"]).max().values
    ll_m = c.shift(1).rolling(v["m"]).min().values
    cur = 0.0
    cv = c.values
    for t in range(n):
        if cur == 0.0:
            if cv[t] > hh_n[t]:
                cur = 1.0
            elif v["short"] and cv[t] < ll_n[t]:
                cur = -1.0
        elif cur == 1.0 and cv[t] < ll_m[t]:
            cur = 0.0
        elif cur == -1.0 and cv[t] > hh_m[t]:
            cur = 0.0
        pos[t] = cur
    return pos


def portfolio(v: dict | None, cost: float, funding: float, lo: str, hi: str, assets=None):
    rets, pos_all, trades = [], [], []
    for a in (assets or ASSETS):
        df = load(a)
        r = df["open"].shift(-1) / df["open"] - 1  # abierto t -> abierto t+1
        if v is None:  # comprar y mantener
            pos = pd.Series(1.0, index=df.index)
        else:
            sig = pd.Series(signals(df, v), index=df.index)
            pos = sig.shift(1).fillna(0.0)  # senal de t-1 -> posicion desde la apertura de t
        turn = pos.diff().abs().fillna(pos.abs())
        if v is None:
            turn = turn * 0
        pnl = pos * r - turn * cost - pos * funding
        rets.append(pnl.rename(a))
        pos_all.append(pos.rename(a))
        if v is not None:  # operaciones individuales (sin costes) para PF/win%
            seg = (pos != pos.shift(1)).cumsum()
            for _, g in pd.DataFrame({"p": pos, "r": r}).groupby(seg):
                if g["p"].iloc[0] != 0 and pd.Timestamp(lo, tz="UTC") <= g.index[0] <= pd.Timestamp(hi, tz="UTC"):
                    trades.append(np.prod(1 + g["p"] * g["r"].fillna(0)) - 1)
    R = pd.concat(rets, axis=1).loc[lo:hi].dropna()
    port = R.mean(axis=1)  # 1/3 por activo
    expo = pd.concat(pos_all, axis=1).loc[lo:hi].abs().mean().mean()
    return port, expo, trades


def metrics(port: pd.Series, expo: float, trades: list) -> dict:
    eq = (1 + port).cumprod()
    yrs = len(port) / 365.0
    cagr = eq.iloc[-1] ** (1 / yrs) - 1
    dd = (eq / eq.cummax() - 1).min()
    sh = port.mean() / port.std() * np.sqrt(365) if port.std() > 0 else np.nan
    t = np.array(trades) if trades else np.array([])
    pf = t[t > 0].sum() / -t[t <= 0].sum() if len(t) and (t <= 0).any() else np.nan
    return dict(cagr_pct=round(100 * cagr, 1), max_dd_pct=round(100 * dd, 1), sharpe=round(sh, 2),
                calmar=round(cagr / abs(dd), 2) if dd < 0 else np.nan, expo_pct=round(100 * expo, 0),
                trades=len(t), win_pct=round(100 * (t > 0).mean(), 0) if len(t) else np.nan,
                pf_trades=round(pf, 2) if pf == pf else np.nan, total_pct=round(100 * (eq.iloc[-1] - 1), 0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["is", "oos"])
    ap.add_argument("names", nargs="*")
    ap.add_argument("--cost", type=float, default=0.001)      # 0.05% comision + 0.05% slippage por lado
    ap.add_argument("--funding", type=float, default=0.0003)  # por dia
    a = ap.parse_args()
    lo, hi = ("2020-10-01", "2022-12-31") if a.stage == "is" else ("2023-01-01", "2100-01-01")
    names = a.names or list(VARIANTS)
    rows = []
    port, expo, _ = portfolio(None, a.cost, 0.0, lo, hi)  # B&H sin funding (tenencia pura, referencia)
    rows.append(dict(var="B&H", **metrics(port, expo, [])))
    for nme in names:
        port, expo, tr = portfolio(VARIANTS[nme], a.cost, a.funding, lo, hi)
        rows.append(dict(var=nme, **metrics(port, expo, tr)))
    print(f"ETAPA {a.stage.upper()}  {lo} -> {hi}   (costes {a.cost*100:.2f}%/lado, funding {a.funding*100:.3f}%/dia)")
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()
