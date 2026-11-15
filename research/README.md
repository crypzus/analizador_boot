# Investigacion de estrategias por regimen (solo historico, sin dinero real)

Pregunta: ¿hay una estrategia intradia que funcione mejor en cada regimen (alcista, lateral, bajista) y que siga
funcionando fuera de los datos con los que se eligio?

```powershell
.\.venv\Scripts\python.exe research\test_research.py        # pruebas sin red
.\.venv\Scripts\python.exe research\run_research.py         # baja/actualiza velas 15m (research\data) y genera el informe
.\.venv\Scripts\python.exe research\run_research.py --no-download
```
Salida: `research\out\informe_regimenes.md` y `research\out\trades_base.csv`.

## Diseno
- **Regimen (4h):** distancia EMA20-EMA50 en ATR; > +0,5 alcista, < -0,5 bajista, en medio lateral.
- **Estrategias** (reglas fijas, ver `strategies.py`): `pullback_ema`, `nivel_roto`, `impulso`, `ruptura_lateral` y un `control`
  al azar con el mismo stop y objetivos. Siempre a favor del regimen; en lateral solo se opera la ruptura.
- **Operacion:** 100 $ x5, mitad en TP1 (1,5 R) y mitad en TP2 (3 R), stop 1,5 ATR(1h), cierre forzado a las 12 h,
  comision 0,05 % por lado. Mismo simulador que el journal (`reporter/setups.py`).
- **Sin mirar el futuro:** una vela de 1h/4h solo se usa desde que cierra; hay pruebas que recortan los datos y comprueban
  que indicadores, regimen y senales anteriores al corte no cambian.
- **Criterio de exito (fijado antes de correr):** una celda pasa con >= 100 operaciones, resultado medio por operacion > 0,
  limite inferior del IC 95 % (remuestreo por meses) > 0 y positivo en >= 2/3 de los trimestres.
- **Walk-forward:** cada mes se elige por regimen la estrategia con mejor resultado en los 2 o 4 meses anteriores y se opera
  solo ese mes; se compara con operar todas y con el control al azar.

## Limites
Datos spot de Binance (no futuros), sin funding ni deslizamiento, velas de 15m (si una vela toca stop y objetivo gana el stop),
umbral de regimen y multiplos de stop/objetivo fijos. Las monedas estan correlacionadas (~0,9 con BTC).
