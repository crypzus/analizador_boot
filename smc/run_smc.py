"""Uso:  python smc/run_smc.py [--pairs SOL ADA] [--fee 0.0005] [--slip 0.0002] [--htf-mode tv|simple]"""
import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from smc_bt import Params, load_ohlcv, run_backtest, summarize  # noqa: E402

DATA = Path(__file__).parent.parent / "user_data" / "data" / "binance" / "futures"
OUT = Path(__file__).parent / "out"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", nargs="+", default=["SOL", "ADA"])
    ap.add_argument("--fee", type=float, default=0.0005)
    ap.add_argument("--slip", type=float, default=0.0002)
    ap.add_argument("--htf-mode", default="tv")
    ap.add_argument("--capital", type=float, default=100.0)
    ap.add_argument("--set", nargs="*", default=[], help="override: nombre=valor (ej. sl_min_atr=0.8)")
    args = ap.parse_args()

    p = Params(fee=args.fee, slip=args.slip, htf_mode=args.htf_mode, capital=args.capital)
    for kv in args.set:
        k, v = kv.split("=")
        cur = getattr(p, k)
        setattr(p, k, type(cur)(v) if not isinstance(cur, bool) else v.lower() in ("1", "true", "yes"))

    OUT.mkdir(exist_ok=True)
    rows = []
    for pair in args.pairs:
        df = load_ohlcv(str(DATA / f"{pair}_USDT_USDT-15m-futures.feather"))
        tr = run_backtest(df, p)
        tr.to_csv(OUT / f"trades_{pair}.csv", index=False)
        rows.append(summarize(tr, p.capital, f"{pair} TODO"))
        if not tr.empty:
            tr["year"] = tr["exit_time"].dt.year
            for y, g in tr.groupby("year"):
                rows.append(summarize(g, p.capital, f"{pair} {y}"))
            for t, g in tr.groupby("tag"):
                rows.append(summarize(g, p.capital, f"{pair} tag={t}"))
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()
