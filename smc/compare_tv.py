"""
Compara las operaciones del port (smc_bt.py) con la lista de operaciones exportada del
Strategy Tester de TradingView.

Uso:  python smc/compare_tv.py RUTA_EXPORT.csv|xlsx --pair SOL [--tz UTC] [--tol-min 0]

El export de TradingView trae dos filas por operacion (Entrada y Salida). Se reconocen
encabezados en espanol e ingles. Se compara solo el periodo comun a ambas listas.
"""
import argparse
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from smc_bt import Params, load_ohlcv, run_backtest  # noqa: E402

DATA = Path(__file__).parent.parent / "user_data" / "data" / "binance" / "futures"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower().replace("º", "o").replace("ñ", "n")).strip()


def read_tv(path: str, tz: str) -> pd.DataFrame:
    if path.lower().endswith((".xlsx", ".xls")):
        xl = pd.ExcelFile(path)
        sheet = next((s for s in xl.sheet_names if re.search(r"list|lista|oper|trade", _norm(s))), xl.sheet_names[0])
        raw = xl.parse(sheet)
    else:
        raw = pd.read_csv(path, sep=None, engine="python", encoding="utf-8-sig")
    cols = {_norm(c): c for c in raw.columns}

    def pick(*keys):
        for k, c in cols.items():
            if any(x in k for x in keys):
                return c
        raise SystemExit(f"No encuentro columna {keys}. Columnas: {list(raw.columns)}")

    c_no = pick("trade", "operacion", "n o", "no ")
    c_ty = pick("type", "tipo")
    c_dt = pick("date", "fecha")
    c_px = pick("price", "precio")
    out = []
    for no, g in raw.groupby(c_no, sort=True):
        e = g[g[c_ty].astype(str).str.contains("entry|entrada", case=False, regex=True)]
        x = g[g[c_ty].astype(str).str.contains("exit|salida", case=False, regex=True)]
        if e.empty or x.empty:
            continue
        e, x = e.iloc[0], x.iloc[0]
        side = "L" if re.search(r"long|largo", str(e[c_ty]), re.I) else "S"
        out.append(dict(no=no, side=side,
                        entry_time=pd.Timestamp(e[c_dt]).tz_localize(tz) if pd.Timestamp(e[c_dt]).tzinfo is None else pd.Timestamp(e[c_dt]),
                        entry=float(str(e[c_px]).replace(",", ".")) if isinstance(e[c_px], str) else float(e[c_px]),
                        exit_time=pd.Timestamp(x[c_dt]).tz_localize(tz) if pd.Timestamp(x[c_dt]).tzinfo is None else pd.Timestamp(x[c_dt]),
                        exit=float(str(x[c_px]).replace(",", ".")) if isinstance(x[c_px], str) else float(x[c_px])))
    t = pd.DataFrame(out)
    t["entry_time"] = t["entry_time"].dt.tz_convert("UTC")
    t["exit_time"] = t["exit_time"].dt.tz_convert("UTC")
    return t


def compare(tv: pd.DataFrame, mine: pd.DataFrame, tol_min: int = 0) -> dict:
    lo, hi = tv["entry_time"].min(), tv["entry_time"].max()
    mine = mine[(mine["entry_time"] >= lo) & (mine["entry_time"] <= hi)].reset_index(drop=True)
    tol = pd.Timedelta(minutes=tol_min)
    used = set()
    rows = []
    for _, r in tv.iterrows():
        cand = mine[(mine["side"] == r["side"]) & ((mine["entry_time"] - r["entry_time"]).abs() <= tol)]
        cand = cand[~cand.index.isin(used)]
        if cand.empty:
            rows.append(dict(status="solo_TV", **r.to_dict()))
            continue
        j = cand.index[0]
        used.add(j)
        m = mine.loc[j]
        rows.append(dict(status="match", side=r["side"], entry_time=r["entry_time"],
                         d_entry_pct=(m["entry"] / r["entry"] - 1) * 100,
                         d_exit_pct=(m["exit"] / r["exit"] - 1) * 100,
                         d_exit_min=(m["exit_time"] - r["exit_time"]).total_seconds() / 60))
    only_mine = mine[~mine.index.isin(used)]
    res = pd.DataFrame(rows)
    return dict(table=res, only_mine=only_mine, n_tv=len(tv), n_mine=len(mine),
                n_match=int((res["status"] == "match").sum()) if not res.empty else 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--pair", default="SOL")
    ap.add_argument("--tz", default="UTC", help="zona horaria del export de TradingView (por defecto UTC)")
    ap.add_argument("--tol-min", type=int, default=0)
    ap.add_argument("--fee", type=float, default=0.0)
    ap.add_argument("--slip", type=float, default=0.0)
    a = ap.parse_args()

    tv = read_tv(a.file, a.tz)
    # En la comparacion de entradas se usan los costes del propio TV; ajusta --fee/--slip a lo que tenia el script
    p = Params(fee=a.fee, slip=a.slip, capital=1000.0)
    df = load_ohlcv(str(DATA / f"{a.pair}_USDT_USDT-15m-futures.feather"))
    mine = run_backtest(df, p)
    res = compare(tv, mine, a.tol_min)
    t = res["table"]
    print(f"TV: {res['n_tv']} operaciones | port (mismo periodo): {res['n_mine']} | coinciden: {res['n_match']}")
    if res["n_tv"]:
        print(f"Coincidencia sobre TV: {100 * res['n_match'] / res['n_tv']:.1f}%")
    m = t[t["status"] == "match"] if not t.empty else t
    if len(m):
        print("Diferencia de precio de entrada % (mediana/max abs):", round(m.d_entry_pct.median(), 4), round(m.d_entry_pct.abs().max(), 4))
        print("Diferencia de precio de salida %  (mediana/max abs):", round(m.d_exit_pct.median(), 4), round(m.d_exit_pct.abs().max(), 4))
        print("Salidas en el mismo minuto:", f"{(m.d_exit_min.abs() < 1).mean() * 100:.1f}%")
    print("\nSolo en TV (primeras 10):")
    print(t[t["status"] == "solo_TV"].head(10).to_string(index=False) if not t.empty else "-")
    print("\nSolo en el port (primeras 10):")
    print(res["only_mine"][["side", "tag", "entry_time", "entry", "sl", "tp"]].head(10).to_string(index=False))


if __name__ == "__main__":
    main()
