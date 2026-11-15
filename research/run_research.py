"""Investigacion de estrategias por regimen de mercado (solo datos historicos, sin dinero real).

  python research/run_research.py              descarga/actualiza el historial y genera el informe
  python research/run_research.py --no-download usa solo el cache local (research/data)
Salida: research/out/informe_regimenes.md y research/out/trades_base.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import analysis  # noqa: E402
import backtest  # noqa: E402
import data  # noqa: E402
import features  # noqa: E402
import strategies  # noqa: E402

OUT = Path(__file__).parent / "out"
REG = {"up": "alcista", "down": "bajista", "range": "lateral"}
SCENARIOS = {
    "base": {"stop_k": 1.5, "hold": 12, "fee": 0.0005},
    "comision limite 0,02%": {"stop_k": 1.5, "hold": 12, "fee": 0.0002},
    "stop 1 ATR": {"stop_k": 1.0, "hold": 12, "fee": 0.0005},
    "plazo 24 h": {"stop_k": 1.5, "hold": 24, "fee": 0.0005},
}


def run_scenario(feats: dict, stop_k: float, hold: float, fee: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Devuelve (operaciones, conteo de senales/sin entrada por estrategia)."""
    all_trades, rows = [], []
    for coin, d in feats.items():
        sigs = strategies.generate(d, coin, stop_k)
        for name in strategies.STRATEGIES:
            part = [s for s in sigs if s["strategy"] == name]
            tr, c = backtest.run(d, part, hold_hours=hold, fee=fee)
            rows.append({"strategy": name, **c})
            if len(tr):
                all_trades.append(tr)
    trades = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    counts = pd.DataFrame(rows).groupby("strategy").sum(numeric_only=True)
    return trades, counts


def _f(x, nd=2, pct=False) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "n/d"
    return f"{x * 100:.0f} %" if pct else f"{x:.{nd}f}"


def _md(headers: list, rows: list) -> list[str]:
    return ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)] + ["| " + " | ".join(map(str, r)) + " |" for r in rows]


def build_report(feats: dict, cover: dict, results: dict, counts: pd.DataFrame) -> str:
    base = results["base"]
    L = ["# Estrategias por regimen de mercado (estudio historico, sin dinero real)", "",
         "Operacion de estudio: 100 $ x5 (500 $ de nocional), intradia, a favor del regimen de 4h, mitad en TP1 (1,5 R) "
         "y mitad en TP2 (3 R), stop 1,5 ATR(1h), cierre forzado a las 12 h, comision 0,05 % por lado. "
         "Datos: velas 15m de Binance spot.", "", "## Datos", ""]
    L += _md(["Moneda", "Velas", "Desde", "Hasta", "Huecos"],
             [[c, v["candles"], str(v["from"])[:10], str(v["to"])[:10], v["gaps"]] for c, v in cover.items()])
    L += ["", "## 1. Resultado por estrategia y regimen (escenario base)", "",
          "Criterio fijado antes de correr: PASA solo con >= 100 operaciones, resultado medio por operacion > 0, "
          "limite inferior del IC 95 % > 0 y positivo en >= 2/3 de los trimestres.", ""]
    t = analysis.cell_table(base)
    rows = [[r["strategy"], REG[r["regime"]], r["n"], _f(r["win"], pct=True), _f(r["avg_win"]), _f(r["avg_loss"]),
             _f(r["e"]), _f(r["e_r"]), f"[{_f(r['lo'])} ; {_f(r['hi'])}]", f"{r['q_pos']}/{r['q_n']}", r["verdict"]]
            for r in t.to_dict("records")]
    L += _md(["Estrategia", "Regimen", "Oper.", "Acierto", "Ganancia media $", "Perdida media $", "Resultado/oper. $",
              "R medio", "IC 95 % $", "Trim. positivos", "Veredicto"], rows)
    n_pass = int((t["verdict"] == "PASA").sum()) if len(t) else 0
    L += ["", f"Celdas que pasan el criterio: **{n_pass} de {len(t)}**.", ""]
    L += ["## 2. Entradas que se llenaron (escenario base)", ""]
    cnt = counts.reset_index()
    L += _md(["Estrategia", "Senales", "Sin entrada", "Sin terminar al final de datos"],
             [[r["strategy"], int(r["signals"]), int(r["no_fill"]), int(r["unfinished"])] for r in cnt.to_dict("records")])
    L += ["", "## 3. Resultado medio por operacion ($) por moneda (escenario base)", ""]
    pv = base.pivot_table(index=["strategy", "regime"], columns="coin", values="pnl", aggfunc="mean").round(2)
    L += _md(["Estrategia / regimen"] + list(pv.columns),
             [[f"{i[0]} / {REG[i[1]]}"] + [_f(v) for v in row] for i, row in zip(pv.index, pv.to_numpy())])
    L += ["", "## 4. Sensibilidad: resultado medio por operacion ($) y numero de operaciones", ""]
    names = list(results)
    cells = sorted({(r["strategy"], r["regime"]) for r in t.to_dict("records")})
    srows = []
    for st, rg in cells:
        row = [f"{st} / {REG[rg]}"]
        for nme in names:
            g = results[nme][(results[nme]["strategy"] == st) & (results[nme]["regime"] == rg)]
            row.append(f"{_f(g['pnl'].mean())} ({len(g)})" if len(g) else "n/d")
        srows.append(row)
    L += _md(["Estrategia / regimen"] + names, srows)
    L += ["", "## 5. Ventanas moviles (walk-forward)", "",
          "Cada mes se elige, en cada regimen, la estrategia con mejor resultado en los meses anteriores (si ninguna es "
          "positiva, no se opera) y se opera solo ese mes. Todo lo que se suma es fuera de muestra.", ""]
    for w in (2, 4):
        wf = analysis.walk_forward(base, w)
        L += [f"### Ventana de {w} meses ({wf['months']} meses evaluados)", ""]
        L += _md(["Que se opera", "Oper.", "Acierto", "Resultado/oper. $", "IC 95 % $", "Total $"],
                 [[nm, wf[k]["n"], _f(wf[k]["win"], pct=True), _f(wf[k]["e"]), f"[{_f(wf[k]['lo'])} ; {_f(wf[k]['hi'])}]",
                   _f(wf[k]["total"], 0)] for nm, k in (("Seleccion por ventana", "seleccion"),
                                                          ("Todas las estrategias", "todas"), ("Control al azar", "control"))])
        dec = wf["decisions"].groupby(["regime", "strategy"]).size().reset_index(name="meses")
        L += ["", "Estrategia elegida por regimen (meses):", ""]
        L += _md(["Regimen", "Estrategia", "Meses"], [[REG[r["regime"]], r["strategy"], r["meses"]] for r in dec.to_dict("records")])
        L += [""]
    L += ["## Como leer esto", "",
          "- Si ninguna celda pasa y la seleccion por ventanas no supera al control al azar, con estas reglas no hay ventaja.",
          "- Las monedas estan correlacionadas (~0,9 con BTC): las operaciones de un mismo dia no son independientes; por eso el IC "
          "se calcula remuestreando meses completos.",
          "- Limites: datos spot (no futuros), sin funding ni deslizamiento, velas de 15m (si una vela toca stop y objetivo gana el stop). "
          "El umbral del regimen (0,5 ATR) y los multiplos de stop/objetivo son fijos; la sensibilidad muestra solo cuatro variantes."]
    return "\n".join(L)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-download", action="store_true")
    ap.add_argument("--coins", nargs="*", default=data.COINS)
    a = ap.parse_args()
    feats, cover = {}, {}
    for coin in a.coins:
        if not a.no_download:
            print(f"Actualizando historial de {coin}...", flush=True)
            data.update_cache(coin)
        df = data.load(coin)
        cover[coin] = data.coverage(df)
        feats[coin] = features.build(df)
        print(f"{coin}: {cover[coin]['candles']} velas, {cover[coin]['gaps']} huecos", flush=True)
    results, counts = {}, None
    for name, p in SCENARIOS.items():
        print(f"Escenario {name}...", flush=True)
        results[name], c = run_scenario(feats, p["stop_k"], p["hold"], p["fee"])
        if name == "base":
            counts = c
    OUT.mkdir(parents=True, exist_ok=True)
    results["base"].to_csv(OUT / "trades_base.csv", index=False)
    text = build_report(feats, cover, results, counts)
    (OUT / "informe_regimenes.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
