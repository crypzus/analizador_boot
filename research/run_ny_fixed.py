"""Backtest NY con capital fijo por operacion: 100 $ de margen, apalancamiento 5x (500 $ nocional) y stop fijo
de 5 % o 10 % del capital de la operacion (= 1 % o 2 % del precio, perdida de 5 $ o 10 $). Parametros fijados antes.
Uso: python research/run_ny_fixed.py -> research/out/ny_fixed_report.md, ny_fixed_trades.csv, ny_fixed_equity.png
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import data  # noqa: E402
import ny_breakout as ny  # noqa: E402

OUT = HERE / "out"
OUT.mkdir(exist_ok=True)
MARGIN, LEV = 100.0, 5.0
NOTIONAL = MARGIN * LEV
STOPS = {"stop 5% del capital (1% del precio, 5 $)": 0.01, "stop 10% del capital (2% del precio, 10 $)": 0.02}
L: list[str] = []


def say(s: str = "") -> None:
    print(s)
    L.append(s)


def f(x, d=2):
    return "-" if x is None or (isinstance(x, float) and x != x) else f"{x:.{d}f}"


def money(df: pd.DataFrame, stop: float) -> np.ndarray:
    return df.sort_values("entry_ts")["r"].to_numpy() * NOTIONAL * stop     # R x dolares arriesgados por operacion


def row(name: str, df: pd.DataFrame, stop: float) -> str:
    if df.empty:
        return f"| {name} | 0 | | | | | | |"
    s, usd = ny.summarize(df), money(df, stop)
    eq = np.r_[0.0, np.cumsum(usd)]
    dd = (np.maximum.accumulate(eq) - eq).max()
    return (f"| {name} | {s['n']} | {s['win']*100:.0f}% | {f(s['e'],3)} | {f(s['pf'])} | {usd.sum():+.0f} $ | {dd:.0f} $ "
            f"| {f(s['lo'],3)} / {f(s['hi'],3)} |")


HEAD = ("| Variante | Op. | Acierto | Esperanza R | PF | Resultado total | Caida max | IC95 media R |\n"
        "|---|---|---|---|---|---|---|---|")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    df = data.load("BTCUSDT")
    a = ny.prepare(df)
    say("# Backtest NY BTC/USDT con 100 $ x5 y stop fijo (5 anos)\n")
    say(f"Capital por operacion {MARGIN:.0f} $, apalancamiento {LEV:.0f}x ({NOTIONAL:.0f} $ nocional), sin interes compuesto. "
        "Mismas reglas de sesgo, ventana, gatillos y gestion de la v1.1; solo cambia el stop (fijo en % del precio, no en ATR). "
        "Comision 0,05 % por lado + 0,01 % de deslizamiento + funding 0,01 % cada 8 h.\n")
    best_trades, curves = {}, {}
    for label, sp in STOPS.items():
        P = ny.params(stop_pct=sp)
        prim, cnt = ny.run(a, P)
        risk = NOTIONAL * sp
        say(f"## {label}\n")
        ratio = 2 * (ny.FEE + ny.SLIP) / sp
        say(f"Costo ida y vuelta = {2*(ny.FEE+ny.SLIP)*100:.2f}% del precio = {ratio*100:.0f}% del riesgo. Liquidacion a ~{100/LEV:.0f}% "
            f"del precio (lejos del stop de {sp*100:.0f}%). Senales {cnt['senales']}; descartadas por posicion abierta "
            f"{cnt['ocupado']}, maximo diario {cnt['max_dia']}, parada por perdidas {cnt['parada_perdidas']}.\n")
        say(HEAD)
        y3 = prim[prim["entry_ts"] >= pd.Timestamp("2023-11-17", tz="UTC")]
        variants = {"estrategia (ruptura+reversion)": prim, "solo ruptura": ny.run(a, ny.params(stop_pct=sp, mode="brk"))[0],
                    "solo reversion": ny.run(a, ny.params(stop_pct=sp, mode="rev"))[0], "ultimos 3 anos": y3,
                    "control: solo sesgo (13:00 UTC)": ny.run(a, ny.params(stop_pct=sp, mode="bias_only"))[0],
                    "costos x2": ny.run(a, ny.params(stop_pct=sp, cost_mult=2.0))[0],
                    "cierre 22:00 UTC (informativa)": ny.run(a, ny.params(stop_pct=sp, hold_mode="session_end"))[0]}
        for k, v in variants.items():
            say(row(k, v, sp))
        rnd = [ny.run(a, ny.params(stop_pct=sp, mode="random", seed=sd))[0] for sd in range(20)]
        means = np.array([x["r"].mean() for x in rnd])
        s = ny.summarize(prim)
        say(f"\nControl al azar (20 semillas): esperanza media {means.mean():.3f} R (rango {means.min():.3f} a {means.max():.3f}); "
            f"la estrategia ({s['e']:.3f} R) supera a {(means < s['e']).mean()*100:.0f}% de ellas.")
        say(f"Antes de costos: {prim['r_gross'].mean():.3f} R por operacion; costos+funding: {prim['cost_r'].mean():.3f} R por operacion.")
        say("Salidas: " + ", ".join(f"{k} {v}" for k, v in prim["reason"].value_counts().items())
            + f"; duracion mediana {prim['hold_h'].median():.1f} h.")
        yrs = prim.groupby("year")["r"].agg(["count", "sum"])
        say("Por ano ($, operaciones): " + ", ".join(f"{int(y)}: {r['sum']*risk:+.0f} $ ({int(r['count'])})" for y, r in yrs.iterrows()))
        say(f"Racha maxima de perdidas: {s['streak']} seguidas = {s['streak']*risk:.0f} $ (con 100 $ de margen por operacion). "
            f"Acierto minimo para no perder con RR {s['rr']:.2f}: {s['be_win']*100:.0f}% (real {s['win']*100:.0f}%).")
        full = yrs.loc[[y for y in (2022, 2023, 2024, 2025) if y in yrs.index]]
        crit = [("100 o mas operaciones", s["n"] >= 100, str(s["n"])), ("profit factor > 1,3", s["pf"] > 1.3, f(s["pf"])),
                ("IC95 inferior de la media > 0", s["lo"] > 0, f(s["lo"], 3)),
                (">= 2/3 de trimestres positivos", s["q_n"] > 0 and s["q_pos"] / s["q_n"] >= 2 / 3, f"{s['q_pos']}/{s['q_n']}"),
                ("a lo sumo 1 de 4 anos en perdidas", int((full["sum"] < 0).sum()) <= 1, f"{int((full['sum'] < 0).sum())} de {len(full)}")]
        say("\nCriterios fijados antes (parametros fijos, 5 anos):")
        for k, ok, v in crit:
            say(f"- {'CUMPLE' if ok else 'NO CUMPLE'}: {k} ({v})")
        say(f"- Resultado: **{'APRUEBA' if all(c[1] for c in crit) else 'NO APRUEBA'}**\n")
        best_trades[label] = prim
        curves[label] = (prim, variants["control: solo sesgo (13:00 UTC)"], sp)
    pd.concat([v.assign(stop=k) for k, v in best_trades.items()]).to_csv(OUT / "ny_fixed_trades.csv", index=False)
    say("Limites: velas spot (no perpetuo); costos y funding supuestos; lo que se decide a ojo no se prueba; en real se suele "
        "acertar menos; no es una prediccion.")
    (OUT / "ny_fixed_report.md").write_text("\n".join(L), encoding="utf-8")
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(11, 5))
        for label, (p, c, sp) in curves.items():
            for nm, v, ls in ((f"{label.split(' (')[0]} - estrategia", p, "-"), (f"{label.split(' (')[0]} - solo sesgo", c, "--")):
                v = v.sort_values("entry_ts")
                ax.plot(v["exit_ts"], np.cumsum(v["r"].to_numpy()) * NOTIONAL * sp, ls, label=nm)
        ax.axhline(0, color="gray", lw=0.5); ax.set_ylabel("$ acumulados (100 $ x5 por operacion)"); ax.legend(fontsize=8)
        fig.tight_layout(); fig.savefig(OUT / "ny_fixed_equity.png", dpi=110)
    except Exception as exc:  # noqa: BLE001
        print("sin grafica:", exc)


if __name__ == "__main__":
    main()
