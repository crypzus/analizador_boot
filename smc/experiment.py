"""
Protocolo (fijado ANTES de ver resultados):
  - Variantes (cerradas, no se anaden mas): V0..V5 abajo.
  - ETAPA 1 (seleccion): solo operaciones con entrada 2023-01-01..2024-12-31.
  - Criterio de seleccion: PF(R) > 1.10 en SOL y en ADA por separado, y >= 150 operaciones sumando ambos.
  - ETAPA 2 (validacion, una sola vez): 2025-01-01..hoy, solo las variantes que pasaron la etapa 1.
  - Metricas en R (independientes del capital). Costes: fee 0.05% por lado + slippage 0.02%.
Uso:  python smc/experiment.py is | oos V1 V3
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from smc_bt import Params, build_context, load_ohlcv, run_backtest  # noqa: E402

DATA = Path(__file__).parent.parent / "user_data" / "data" / "binance" / "futures"

VARIANTS = {
    "V0": {},                                                       # base
    "V1": dict(sl_min_atr=0.8),
    "V2": dict(sl_min_atr=1.0),
    "V3": dict(sl_min_atr=0.8, enable_ob=False),                    # solo barrido
    "V4": dict(sl_min_atr=0.8, enable_ob=False, cost_aware=True, range_min_rr=2.0),
    "V5": dict(sl_min_atr=1.0, cost_aware=True, min_rr=2.5, range_min_rr=2.0),
}


def stats(t: pd.DataFrame) -> dict:
    if t.empty:
        return dict(n=0, win=0, pf=np.nan, avgR=np.nan, sumR=0)
    w, l = t.loc[t.r > 0, "r"].sum(), -t.loc[t.r <= 0, "r"].sum()
    return dict(n=len(t), win=round(100 * (t.r > 0).mean(), 1), pf=round(w / l, 2) if l > 0 else np.inf,
                avgR=round(t.r.mean(), 3), sumR=round(t.r.sum(), 1))


def main():
    stage = sys.argv[1]
    names = sys.argv[2:] or list(VARIANTS)
    lo, hi = ("2023-01-01", "2025-01-01") if stage == "is" else ("2025-01-01", "2100-01-01")
    lo, hi = pd.Timestamp(lo, tz="UTC"), pd.Timestamp(hi, tz="UTC")
    data = {k: load_ohlcv(str(DATA / f"{k}_USDT_USDT-15m-futures.feather")) for k in ("SOL", "ADA")}
    rows = []
    base = Params()
    ctx = {k: build_context(d, base) for k, d in data.items()}
    for name in names:
        p = Params(**{**base.__dict__, **VARIANTS[name]})
        allt = []
        for pair, df in data.items():
            t = run_backtest(df, p, ctx[pair])
            t = t[(t.entry_time >= lo) & (t.entry_time < hi)]
            rows.append(dict(var=name, pair=pair, **stats(t)))
            allt.append(t)
        rows.append(dict(var=name, pair="AMBOS", **stats(pd.concat(allt))))
    print(f"ETAPA {stage.upper()}  {lo.date()} -> {hi.date()}")
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()
