"""Backtest de 5 anos de la estrategia NY (ver ny_breakout.py). Uso: python research/run_ny.py
Salidas (ignoradas por git): research/out/ny_trades.csv, ny_report.md, ny_equity.png
"""
from __future__ import annotations

import itertools
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
DD_LIMIT, DAILY_LIMIT, TARGET = 20.0, 5.0, 10.0
L: list[str] = []


def say(s: str = "") -> None:
    print(s)
    L.append(s)


def f(x, d=2):
    return "-" if x is None or (isinstance(x, float) and x != x) else f"{x:.{d}f}"


def row(name: str, s: dict) -> str:
    if not s.get("n"):
        return f"| {name} | 0 | | | | | | |"
    return (f"| {name} | {s['n']} | {s['win']*100:.0f}% | {f(s['e'],3)} | {f(s['pf'])} | {f(s['total_pct'],1)}% "
            f"| {f(s['max_dd'],1)}% | {f(s['lo'],3)} / {f(s['hi'],3)} |")


HEAD = "| Variante | Op. | Acierto | Esperanza R | PF | Total (cuenta) | Caida max | IC95 media R |\n|---|---|---|---|---|---|---|---|"


def sem(v, green, yellow, higher_better=True):
    ok_g = v >= green if higher_better else v <= green
    ok_y = v >= yellow if higher_better else v <= yellow
    return "🟢" if ok_g else ("🟡" if ok_y else "🔴")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    df = data.load("BTCUSDT")
    cov = data.coverage(df)
    a = ny.prepare(df)
    say("# Backtest NY BTC/USDT v1.1 (5 anos)\n")
    say(f"Datos: Binance spot BTCUSDT 15m, {cov['candles']} velas, {cov['from']:%Y-%m-%d} a {cov['to']:%Y-%m-%d} UTC, "
        f"{cov['gaps']} hueco(s) (6 velas, 2023-03-24). Hora: UTC; la ventana es 13:00-22:00 UTC (08:00-17:00 Lima).")
    bd = a["bias_day"].iloc[40:]
    say(f"Dias con sesgo: largo {(bd==1).mean()*100:.0f}%, corto {(bd==-1).mean()*100:.0f}%, sin operar {(bd==0).mean()*100:.0f}% "
        f"({len(bd)} dias).\n")

    P = ny.params()
    prim, cnt = ny.run(a, P)
    say(f"Senales evaluadas {cnt['senales']}; descartadas: posicion abierta {cnt['ocupado']}, maximo diario {cnt['max_dia']}, "
        f"parada por perdidas {cnt['parada_perdidas']}, sin cerrar al final de los datos {cnt['sin_cerrar']}.\n")
    s = ny.summarize(prim)
    y3 = prim[prim["entry_ts"] >= pd.Timestamp("2023-11-17", tz="UTC")]
    by = {"ruptura+reversion (estrategia)": prim, "solo ruptura": ny.run(a, ny.params(mode="brk"))[0],
          "solo reversion": ny.run(a, ny.params(mode="rev"))[0], "ultimos 3 anos (estrategia)": y3}
    say("## 1. Parametros fijos (sin optimizar), 5 anos\n")
    say(HEAD)
    for k, v in by.items():
        say(row(k, ny.summarize(v)))
    say("\nPor gatillo dentro de la estrategia: " + ", ".join(
        f"{t}: {len(g)} op., esperanza {g['r'].mean():.3f} R" for t, g in prim.groupby("trigger")))
    say("Salidas: " + ", ".join(f"{k} {v}" for k, v in prim["reason"].value_counts().items()))
    say(f"Antes de costos: {s['gross_r']:.1f} R; costos+funding: {prim['cost_r'].sum():.1f} R "
        f"(el costo medio por operacion es {prim['cost_r'].mean():.3f} R).")
    say("Por ano (R): " + ", ".join(f"{k}: {v:+.1f}" for k, v in s["years"].items()))

    say("\n## 2. Controles y variantes\n")
    say(HEAD)
    ctl = {"control: solo sesgo (entra 13:00 UTC)": ny.run(a, ny.params(mode="bias_only"))[0],
           "variante: costos x2": ny.run(a, ny.params(cost_mult=2.0))[0],
           "variante: sin operar si costo > 15% del stop": ny.run(a, ny.params(min_cost_ratio=0.15))[0],
           "variante: cierre 22:00 UTC (informativa)": ny.run(a, ny.params(hold_mode="session_end"))[0]}
    for k, v in ctl.items():
        say(row(k, ny.summarize(v)))
    rnd = [ny.run(a, ny.params(mode="random", seed=sd))[0] for sd in range(20)]
    means = np.array([x["r"].mean() for x in rnd])
    say(f"\nControl al azar (1 entrada aleatoria por dia con sesgo, 20 semillas): esperanza media {means.mean():.3f} R "
        f"(rango {means.min():.3f} a {means.max():.3f}), {np.mean([len(x) for x in rnd]):.0f} operaciones cada una. "
        f"La estrategia ({s['e']:.3f} R) supera a {(means < s['e']).mean()*100:.0f}% de las 20 corridas al azar.")

    grid = {f"sl{sl}/n{lb}": ny.run(a, ny.params(sl_mult=sl, lookback=lb))[0] for sl, lb in itertools.product([1.0, 1.2, 1.5], [8, 12, 16])}
    say("\n## 3. Rejilla fija (9 combinaciones, 5 anos) y walk-forward 6+2 meses\n")
    say(HEAD)
    for k, v in grid.items():
        say(row(k, ny.summarize(v)))
    wf = ny.walk_forward(grid, "sl1.2/n12")
    say("\nWalk-forward (solo operaciones fuera de muestra):\n")
    say(HEAD)
    say(row("walk-forward (se queda fuera si la mejor <= 0)", ny.summarize(wf["sel"])))
    say(row("walk-forward (opera siempre la mejor)", ny.summarize(wf["sel_all"])))
    say(row("parametros fijos 1.2/12, mismas ventanas", ny.summarize(wf["fixed"])))
    pool = wf["pool"]
    med = pool.groupby("combo")["r"].mean().median() if len(pool) else float("nan")
    say(f"\nMediana de la esperanza de las 9 combinaciones en esas ventanas: {med:.3f} R. "
        f"Ventanas: {len(wf['decisions'])}, de las que se quedo fuera: {int(wf['decisions']['stay_out'].sum())}.")
    say("Combinaciones elegidas: " + ", ".join(f"{k} x{v}" for k, v in wf["decisions"]["combo"].value_counts().items()))

    say("\n## 4. Metricas de la estrategia con parametros fijos (5 anos)\n")
    days = ny.daily_r(prim, prim["entry_ts"].min(), prim["exit_ts"].max())
    mc = ny.monte_carlo(days, dd_limit=DD_LIMIT, target=TARGET, daily_limit=DAILY_LIMIT)
    yrs = prim.groupby("year")["r"].agg(["count", "sum"])
    full = yrs.loc[[y for y in (2022, 2023, 2024, 2025) if y in yrs.index]]
    neg = int((full["sum"] < 0).sum())
    n_ok = sem(s["n"], 100, 50) if (full["count"] >= 20).all() else ("🟡" if s["n"] >= 50 else "🔴")
    tab = [
        ("1 Operaciones", f"{s['n']} ({s['n']/60:.1f} al mes; por ano completo: {', '.join(str(int(x)) for x in full['count'])})", n_ok),
        ("2 Acierto", f"{s['win']*100:.0f}%", ""),
        ("3 RR real / acierto minimo / esperanza", f"RR {f(s['rr'])}; minimo {s['be_win']*100:.0f}%; esperanza {s['e']:.3f} R",
         sem(s["e"], 0.1, 0.0)),
        ("4 Profit factor", f"{f(s['pf'])}" + (" (>3: sospecha de error)" if s["pf"] > 3 else ""), ""),
        ("5 Caida maxima", f"{s['max_dd']:.1f}% de la cuenta", sem(s["max_dd"], DD_LIMIT / 2, DD_LIMIT, False)),
        ("6 Racha de perdidas", f"{s['streak']} seguidas = {s['streak_pct']:.1f}%", ""),
        ("7 P(pasar caida 20% en 90 dias)", f"{mc['p_dd']*100:.1f}%", sem(mc["p_dd"], 0.05, 0.15, False)),
        ("8 P(llegar a +10% en 90 dias) / antes de romper 20%", f"{mc['p_target']*100:.1f}% / {mc['p_target_first']*100:.1f}%", ""),
        ("9 P(romper limite diario 5%)", f"{mc['p_daily']*100:.1f}%", sem(mc["p_daily"], 0.02, 0.10, False)),
        ("10 P(60 dias o mas sin maximo nuevo)", f"{mc['p_60d']*100:.1f}%", ""),
        ("11 Ano a ano (R, anos completos)", ", ".join(f"{int(y)}: {v:+.1f}" for y, v in full["sum"].items()) + f" -> {neg} de {len(full)} en perdidas",
         "🟢" if neg <= len(full) / 4 else ("🟡" if neg < len(full) / 2 else "🔴")),
    ]
    say("| Metrica | Resultado | Semaforo |\n|---|---|---|")
    for m, r, sm in tab:
        say(f"| {m} | {r} | {sm} |")
    reds = [t[2] for t in (tab[2], tab[6], tab[10])]
    yellows = sum(t[2] == "🟡" for t in tab)
    verdict = "🔴 DESCARTA tal cual" if "🔴" in reds else ("🟢 SIGUE" if yellows <= 1 else "🟡 REVISA")
    say(f"\nVeredicto con el semaforo: **{verdict}**")
    oos = wf["sel"] if len(wf["sel"]) else wf["sel_all"]
    so = ny.summarize(oos)
    crit = [("fuera de muestra >= 100 operaciones", so.get("n", 0) >= 100, f"{so.get('n', 0)}"),
            ("profit factor > 1.3", so.get("pf", 0) > 1.3, f(so.get("pf"))),
            ("caida maxima <= 20%", so.get("max_dd", 99) <= 20, f(so.get("max_dd"), 1) + "%"),
            ("IC95 inferior de la media > 0", (so.get("lo") or -1) > 0, f(so.get("lo"), 3)),
            (">= 2/3 de trimestres positivos", so.get("q_n", 0) > 0 and so["q_pos"] / so["q_n"] >= 2 / 3,
             f"{so.get('q_pos', 0)}/{so.get('q_n', 0)}")]
    say("\nCriterios de aprobacion fijados antes (walk-forward fuera de muestra):\n")
    for k, ok, v in crit:
        say(f"- {'CUMPLE' if ok else 'NO CUMPLE'}: {k} ({v})")
    say(f"- Resultado: **{'APRUEBA' if all(c[1] for c in crit) else 'NO APRUEBA'}**")

    say("\n## 5. Revision de 3 operaciones al azar (velas que disparan y cierran)\n")
    pick = prim.sample(3, random_state=7).sort_values("entry_ts")
    ts_idx = pd.Series(np.arange(a["n"]), index=a["ts"])
    for _, t in pick.iterrows():
        e, x = int(t["entry_idx"]), int(t["exit_idx"])
        say(f"- {t['entry_ts']:%Y-%m-%d %H:%M} UTC, {t['side']} ({t['trigger']}), entrada {t['entry']:.2f}, stop {t['stop']:.2f}, "
            f"TP1 {t['tp1']:.2f}, sale {t['exit_ts']:%Y-%m-%d %H:%M} por {t['reason']}, R neto {t['r']:+.2f}")
        for k in range(e - 1, min(x + 1, e + 4) + 1):
            say(f"    {a['ts'].iloc[k]:%m-%d %H:%M}  O {a['o'][k]:.2f}  H {a['h'][k]:.2f}  L {a['l'][k]:.2f}  C {a['c'][k]:.2f}"
                f"  V {a['v'][k]:.0f}  EMA50 {a['ema'][k]:.2f}  ATR {a['atr'][k]:.2f}")

    say("\n## Lectura\n")
    say("(Se escribe despues de ver las cifras; ver el mensaje de Claude.)")
    say("\nLimites: velas spot (no perpetuo); costos y funding supuestos; lo que se decide a ojo no se prueba; "
        "en real se suele acertar menos que en el backtest; no es una prediccion.")

    prim.to_csv(OUT / "ny_trades.csv", index=False)
    (OUT / "ny_report.md").write_text("\n".join(L), encoding="utf-8")
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(2, 1, figsize=(11, 7), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
        series = {"estrategia (fijos)": prim, "solo sesgo": ctl["control: solo sesgo (entra 13:00 UTC)"],
                  "solo ruptura": by["solo ruptura"], "walk-forward": wf["sel_all"]}
        for k, v in series.items():
            if len(v):
                v = v.sort_values("entry_ts")
                ax[0].plot(v["exit_ts"], ny.equity_pct(v["r"].to_numpy()), label=k)
        ax[0].axhline(0, color="gray", lw=0.5); ax[0].set_ylabel("% de la cuenta (0,75% por op., sin compuesto)"); ax[0].legend()
        e = np.r_[0, ny.equity_pct(prim["r"].to_numpy())]
        ax[1].fill_between(prim["exit_ts"], -(np.maximum.accumulate(e) - e)[1:], 0, color="tab:red", alpha=0.5)
        ax[1].set_ylabel("caida desde maximo (%)")
        fig.tight_layout(); fig.savefig(OUT / "ny_equity.png", dpi=110)
    except Exception as exc:  # noqa: BLE001
        print("sin grafica:", exc)


if __name__ == "__main__":
    main()
