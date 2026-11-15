"""Ronda 2: posicionamiento (funding y long/short) y prediccion de volatilidad. Solo historico, sin dinero real.

  python research/run_round2.py              baja metricas de futuros que falten y genera el informe
  python research/run_round2.py --no-download
Salida: research/out/informe_posicionamiento_volatilidad.md
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import data  # noqa: E402
import positioning as pos  # noqa: E402
import volatility as vol  # noqa: E402
from run_research import _f, _md  # noqa: E402

OUT = Path(__file__).parent / "out"
EXT = {"alto": "alto (short contrario)", "bajo": "bajo (long contrario)"}


def part_a(coins: list[str], no_download: bool) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    events, base, cover = [], {h: [] for h in pos.HORIZONS}, {}
    for coin in coins:
        if not no_download:
            print(f"Metricas de {coin}...", flush=True)
            pos.update_metrics(coin)
        px, m, fd = data.load(coin), pos.load_metrics(coin), pos.load_funding(coin)
        feats = {"funding": fd["value"].to_numpy(dtype=float), "long_short": pos.at_times(fd["ts"], m, "count_long_short_ratio")}
        cover[coin] = {"funding": len(fd), "ls_ok": int(np.isfinite(feats["long_short"]).sum()),
                       "metrics_from": str(m["create_time"].iloc[0])[:10], "metrics_to": str(m["create_time"].iloc[-1])[:10]}
        ev = pos.build_events(coin, px, fd["ts"], feats)
        if len(ev):
            events.append(ev)
        for h, r in pos.baseline_returns(px, fd["ts"]).items():
            base[h].append(r)
    ev = pd.concat(events, ignore_index=True)
    return ev, pos.summarize_cells(ev, {h: np.concatenate(v) for h, v in base.items()}), cover


def part_b(coins: list[str]) -> tuple[pd.DataFrame, dict]:
    res = pd.concat([vol.walk_forward(vol.samples(data.load(c)), c) for c in coins], ignore_index=True)
    return res, vol.summarize(res)


def build_report(cover: dict, cells: pd.DataFrame, vres: pd.DataFrame, vsum: dict) -> str:
    L = ["# Ronda 2: posicionamiento y volatilidad (estudio historico, sin dinero real)", "",
         "## Datos", ""]
    L += _md(["Moneda", "Eventos de funding", "Con ratio L/S", "Metricas desde", "Metricas hasta"],
             [[c, v["funding"], v["ls_ok"], v["metrics_from"], v["metrics_to"]] for c, v in cover.items()])
    L += ["", "## A. Posicionamiento extremo (hipotesis contraria)", "",
          "Evento = valor fuera del percentil 95/5 de los 90 dias previos. Operacion de 100 $ x5 (500 $) al precio spot, "
          "comision 0,1 % ida y vuelta, sin solapar. IC del 98,75 % (Bonferroni por 4 celdas primarias, remuestreo por meses). "
          "PASA: >= 100 eventos, neto medio > 0, IC inferior > 0 y positivo en >= 2/3 de los trimestres.", ""]
    rows = []
    for r in cells.sort_values(["horizon", "feature", "extreme"]).to_dict("records"):
        rows.append([pos.FEATURES[r["feature"]], EXT[r["extreme"]], f"{r['horizon']} h", r["n"], _f(r["gross"] * 100) + " %",
                     _f(r["net"] * 100) + " %", _f(r["base"] * 100) + " %", _f(r["e"]), f"[{_f(r['lo'])} ; {_f(r['hi'])}]",
                     f"{r['q_pos']}/{r['q_n']}", r["verdict"]])
    L += _md(["Variable", "Extremo", "Horizonte", "Eventos", "Bruto medio", "Neto medio", "Deriva (misma direccion)",
              "Neto $/oper.", "IC 98,75 % $", "Trim. positivos", "Veredicto"], rows)
    prim = cells[cells["horizon"] == pos.PRIMARY_H]
    n_pass_a = int((prim["verdict"] == "PASA").sum())
    L += ["", f"Celdas primarias (24 h) que pasan: **{n_pass_a} de {len(prim)}**. "
              "Los horizontes de 8 y 72 h son secundarios: no cuentan para el veredicto.", "",
          "## B. Prediccion de volatilidad (rango de las proximas 24 h)", "",
          f"Modelo lineal en logs con rango de 24 h, media de 7 dias y media de 30 dias, entrenado solo con datos anteriores a cada mes. "
          f"{vsum['n_months']} meses evaluados, {vsum['n']} predicciones (cada 4 h, 4 monedas).", ""]
    f = vsum["rmse_factor"]
    L += _md(["Pronostico", "Error cuadratico medio (logs)", "Error tipico (factor)"],
             [["Modelo HAR", _f(vsum["mse"]["mse_har"], 4), f"x{f['mse_har']:.2f}"],
              ["Persistencia (rango de ayer)", _f(vsum["mse"]["mse_pers"], 4), f"x{f['mse_pers']:.2f}"],
              ["Media de 30 dias", _f(vsum["mse"]["mse_m30"], 4), f"x{f['mse_m30']:.2f}"]])
    L += ["", f"Reduccion del error frente a la mejor referencia ({'persistencia' if vsum['best'] == 'mse_pers' else 'media 30 d'}): "
              f"**{vsum['reduction'] * 100:.1f} %** (IC 95 % [{vsum['lo'] * 100:.1f} % ; {vsum['hi'] * 100:.1f} %]). "
              f"Gana a ambas referencias en {vsum['win_share'] * 100:.0f} % de los meses-moneda. Veredicto: **{vsum['verdict']}**.", ""]
    pc = vres.groupby("coin").apply(lambda g: pd.Series({
        "har": (g["mse_har"] * g["n"]).sum() / g["n"].sum(), "pers": (g["mse_pers"] * g["n"]).sum() / g["n"].sum(),
        "m30": (g["mse_m30"] * g["n"]).sum() / g["n"].sum()}), include_groups=False).reset_index()
    L += _md(["Moneda", "ECM modelo", "ECM persistencia", "ECM media 30 d"],
             [[r["coin"], _f(r["har"], 4), _f(r["pers"], 4), _f(r["m30"], 4)] for r in pc.to_dict("records")])
    L += ["", "## Como leer esto", "",
          "- El error tipico en factor significa: el rango real de las proximas 24 h suele estar entre el pronostico dividido y "
          "multiplicado por ese factor.",
          "- Predecir volatilidad no da direccion: sirve para stops, tamano de posicion y saber cuando conviene no operar.",
          "- Limites: precios spot (los extremos de funding y L/S son de futuros), sin deslizamiento, 4 monedas correlacionadas, "
          "umbrales y ventanas fijos. Del funding de futuros solo hay datos hasta el 4 nov 2026 en el cache local."]
    return "\n".join(L)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-download", action="store_true")
    ap.add_argument("--coins", nargs="*", default=data.COINS)
    a = ap.parse_args()
    print("Parte A...", flush=True)
    ev, cells, cover = part_a(a.coins, a.no_download)
    print("Parte B...", flush=True)
    vres, vsum = part_b(a.coins)
    OUT.mkdir(parents=True, exist_ok=True)
    ev.to_csv(OUT / "eventos_posicionamiento.csv", index=False)
    text = build_report(cover, cells, vres, vsum)
    (OUT / "informe_posicionamiento_volatilidad.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
