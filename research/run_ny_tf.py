"""Backtest NY con gatillos de ruptura en velas de 1h y 4h, 100 $ x5 y stop fijo de 1 % o 2 % del precio (5 % / 10 % del
capital). Parametros fijados antes de ver resultados:
  gatillo: cierre de la vela tf fuera del max/min de las 12 velas previas con volumen > 1.5 x media de 20 previas, a favor
  del sesgo diario; solo ruptura (la reversion tiene muy pocas senales); EMA50 de la vela tf para el trailing (al cierre);
  ventana de entradas 13:00-22:00 UTC; maximo de permanencia 72 h; maximo 3 operaciones al dia y parada tras 2 perdidas.
  4 celdas primarias (2 tf x 2 stops) -> el intervalo de confianza que decide usa 98.75 % (correccion por 4 pruebas).
Uso: python research/run_ny_tf.py -> research/out/ny_tf_report.md, ny_tf_trades.csv, ny_tf_equity.png
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import analysis  # noqa: E402
import data  # noqa: E402
import ny_breakout as ny  # noqa: E402

OUT = HERE / "out"
OUT.mkdir(exist_ok=True)
NOTIONAL = 500.0
CELLS = [("1h", 0.01), ("1h", 0.02), ("4h", 0.01), ("4h", 0.02)]
HOLD72, HOLD24 = 72 * 3600 * 10**9, 24 * 3600 * 10**9
LEVEL = 100 * (1 - 0.05 / 4)
L: list[str] = []


def say(s: str = "") -> None:
    print(s)
    L.append(s)


def f(x, d=2):
    return "-" if x is None or (isinstance(x, float) and x != x) else f"{x:.{d}f}"


HEAD = ("| Variante | Op. | Acierto | Esperanza R | PF | Resultado | Caida max | IC95 media R |\n|---|---|---|---|---|---|---|---|")


def row(name: str, df: pd.DataFrame, sp: float) -> str:
    if df.empty:
        return f"| {name} | 0 | | | | | | |"
    s = ny.summarize(df)
    usd = df.sort_values("entry_ts")["r"].to_numpy() * NOTIONAL * sp
    eq = np.r_[0.0, np.cumsum(usd)]
    return (f"| {name} | {s['n']} | {s['win']*100:.0f}% | {f(s['e'],3)} | {f(s['pf'])} | {usd.sum():+.0f} $ "
            f"| {(np.maximum.accumulate(eq) - eq).max():.0f} $ | {f(s['lo'],3)} / {f(s['hi'],3)} |")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    df = data.load("BTCUSDT")
    a15 = ny.prepare(df)
    say("# Backtest NY BTC/USDT: gatillos de 1h y 4h, 100 $ x5, stop fijo (5 anos)\n")
    say("Mismo sesgo diario, ventana 13:00-22:00 UTC y gestion (50 % en 1R, resto con trailing bajo la EMA50 de la vela del gatillo, "
        "permanencia maxima 72 h). Solo ruptura. La operacion se simula con velas de 15m. Costos: 0,05 % + 0,01 % por lado y funding 0,01 % cada 8 h.\n")
    results, curves = {}, {}
    for tf, sp in CELLS:
        a = ny.prepare_tf(a15, df, tf)
        base = dict(stop_pct=sp, max_hold_ns=HOLD72)
        prim, cnt = ny.run(a, ny.params(**base))
        risk = NOTIONAL * sp
        label = f"gatillo {tf}, stop {sp*500:.0f}% del capital ({sp*100:.0f}% del precio, {risk:.0f} $)"
        say(f"## {label}\n")
        say(f"Costo ida y vuelta = {2*(ny.FEE+ny.SLIP)/sp*100:.0f}% del riesgo. Senales {cnt['senales']}; descartadas por posicion abierta "
            f"{cnt['ocupado']}, maximo diario {cnt['max_dia']}, parada por perdidas {cnt['parada_perdidas']}.\n")
        say(HEAD)
        y3 = prim[prim["entry_ts"] >= pd.Timestamp("2023-11-17", tz="UTC")]
        variants = {"estrategia": prim, "ultimos 3 anos": y3,
                    "control: solo sesgo (13:00 UTC)": ny.run(a, ny.params(**base, mode="bias_only"))[0],
                    "costos x2": ny.run(a, ny.params(**base, cost_mult=2.0))[0],
                    "sin ventana horaria (informativa)": ny.run(a, ny.params(**base, window=False))[0],
                    "permanencia 24 h (informativa)": ny.run(a, ny.params(stop_pct=sp, max_hold_ns=HOLD24))[0]}
        for k, v in variants.items():
            say(row(k, v, sp))
        if prim.empty:
            continue
        rnd = [ny.run(a, ny.params(**base, mode="random", seed=sd))[0] for sd in range(20)]
        means = np.array([x["r"].mean() for x in rnd])
        s = ny.summarize(prim)
        lo_c, hi_c = analysis.boot_ci(prim, "r", level=LEVEL)
        say(f"\nControl al azar (20 semillas): {means.mean():.3f} R de media (rango {means.min():.3f} a {means.max():.3f}); la estrategia "
            f"({s['e']:.3f} R) supera a {(means < s['e']).mean()*100:.0f}% de ellas.")
        say(f"Antes de costos {prim['r_gross'].mean():.3f} R por operacion; costos+funding {prim['cost_r'].mean():.3f} R. "
            f"Largos {int((prim['side']=='long').sum())}, cortos {int((prim['side']=='short').sum())}. "
            f"Duracion mediana {prim['hold_h'].median():.1f} h. Salidas: " + ", ".join(f"{k} {v}" for k, v in prim['reason'].value_counts().items()))
        yrs = prim.groupby("year")["r"].agg(["count", "sum"])
        say("Por ano ($, operaciones): " + ", ".join(f"{int(y)}: {r['sum']*risk:+.0f} $ ({int(r['count'])})" for y, r in yrs.iterrows()))
        say(f"Racha maxima de perdidas: {s['streak']} = {s['streak']*risk:.0f} $. Acierto minimo para no perder con RR {s['rr']:.2f}: "
            f"{s['be_win']*100:.0f}% (real {s['win']*100:.0f}%).")
        full = yrs.loc[[y for y in (2022, 2023, 2024, 2025) if y in yrs.index]]
        crit = [("100 o mas operaciones", s["n"] >= 100, str(s["n"])), ("profit factor > 1,3", s["pf"] > 1.3, f(s["pf"])),
                (f"IC inferior ({LEVEL:.2f} %, corregido por 4 celdas) > 0", lo_c > 0, f(lo_c, 3)),
                (">= 2/3 de trimestres positivos", s["q_n"] > 0 and s["q_pos"] / s["q_n"] >= 2 / 3, f"{s['q_pos']}/{s['q_n']}"),
                ("a lo sumo 1 de 4 anos en perdidas", int((full["sum"] < 0).sum()) <= 1, f"{int((full['sum'] < 0).sum())} de {len(full)}"),
                ("supera al control solo sesgo", s["e"] > variants["control: solo sesgo (13:00 UTC)"]["r"].mean(),
                 f"{s['e']:.3f} vs {variants['control: solo sesgo (13:00 UTC)']['r'].mean():.3f} R")]
        say("\nCriterios fijados antes:")
        for k, ok, v in crit:
            say(f"- {'CUMPLE' if ok else 'NO CUMPLE'}: {k} ({v})")
        say(f"- Resultado: **{'APRUEBA' if all(c[1] for c in crit) else 'NO APRUEBA'}**\n")
        results[label] = prim.assign(cell=label)
        curves[label] = (prim, variants["control: solo sesgo (13:00 UTC)"], sp)
    if results:
        pd.concat(results.values()).to_csv(OUT / "ny_tf_trades.csv", index=False)
    say("Limites: velas spot (no perpetuo); costos y funding supuestos; lo que se decide a ojo no se prueba; en real se suele "
        "acertar menos; no es una prediccion. Con 4 celdas probadas, una que pase por casualidad es posible: por eso el criterio usa IC corregido.")
    (OUT / "ny_tf_report.md").write_text("\n".join(L), encoding="utf-8")
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(11, 5))
        for label, (p, c, sp) in curves.items():
            v = p.sort_values("entry_ts")
            ax.plot(v["exit_ts"], np.cumsum(v["r"].to_numpy()) * NOTIONAL * sp, label=label.split(" (")[0])
        ax.axhline(0, color="gray", lw=0.5); ax.set_ylabel("$ acumulados (100 $ x5 por operacion)"); ax.legend(fontsize=8)
        fig.tight_layout(); fig.savefig(OUT / "ny_tf_equity.png", dpi=110)
    except Exception as exc:  # noqa: BLE001
        print("sin grafica:", exc)


if __name__ == "__main__":
    main()
