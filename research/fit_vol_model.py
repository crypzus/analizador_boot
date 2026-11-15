"""Ajusta el modelo de volatilidad (velas de 1h) y escribe reporter/vol_model.json para el reporter.

  python research/fit_vol_model.py

1) Repite la validacion walk-forward con velas de 1h (la resolucion que usa el reporter en vivo).
2) Solo si PASA el criterio de `volatility.py`, ajusta los coeficientes finales por moneda con TODO el historial
   y guarda tambien el error tipico fuera de muestra (rmse en logs) para dar un rango de incertidumbre.
Volver a ejecutar cuando se quiera reentrenar con datos nuevos.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import data  # noqa: E402
import features  # noqa: E402
import volatility as vol  # noqa: E402

MODEL = Path(__file__).resolve().parent.parent / "reporter" / "vol_model.json"


def hourly(coin: str) -> pd.DataFrame:
    d = data.load(coin)
    return features.resample(d, "1h", 4)[["open_time", "open", "high", "low", "close", "volume"]]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    per, res = {}, []
    for coin in data.COINS:
        s = vol.samples(hourly(coin), minutes=60)
        wf = vol.walk_forward(s, coin)
        res.append(wf)
        n = wf["n"].to_numpy()
        coef = vol._fit(s[vol.FEATS].to_numpy(), s["ly"].to_numpy())
        per[coin] = {"coef": [float(x) for x in coef], "rmse_log": float(np.sqrt((wf["mse_har"] * n).sum() / n.sum())),
                     "n_samples": int(len(s))}
        print(f"{coin}: {len(s)} muestras, coef={np.round(coef, 3).tolist()}, rmse OOS={per[coin]['rmse_log']:.3f}")
    summary = vol.summarize(pd.concat(res, ignore_index=True))
    print(f"Validacion con velas de 1h: reduccion {summary['reduction'] * 100:.1f} % "
          f"(IC95 [{summary['lo'] * 100:.1f} ; {summary['hi'] * 100:.1f}]), gana en {summary['win_share'] * 100:.0f} % "
          f"de los meses-moneda -> {summary['verdict']}")
    if summary["verdict"] != "PASA":
        sys.exit("El modelo no pasa el criterio con velas de 1h: no se escribe vol_model.json")
    out = {"version": 1, "trained_through": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
           "target": "log(log(max alto / min bajo)) de las 24 h siguientes",
           "features": vol.FEATS,
           "oos": {"reduction": summary["reduction"], "ci95": [summary["lo"], summary["hi"]],
                   "win_share": summary["win_share"], "n_months": summary["n_months"]},
           "coins": per}
    MODEL.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Modelo escrito en {MODEL}")


if __name__ == "__main__":
    main()
