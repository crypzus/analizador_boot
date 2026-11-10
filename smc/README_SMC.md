# Port Python de "SMC Entradas 15M" (Pine v5)

Archivos
- `smc_bt.py`   simulador barra a barra (misma logica que el Pine) + metricas.
- `run_smc.py`  corre SOL y ADA (2023-01 a hoy, 15m Binance Futures). Resultados en `out/trades_*.csv`.
- `test_port.py` pruebas de cordura (pivotes, estructura, ausencia de lookahead).

Uso (desde `trading-bot`):
```powershell
.\.venv\Scripts\python.exe smc\test_port.py
.\.venv\Scripts\python.exe smc\run_smc.py                       # defaults del Pine, fee 0.05%, slip 0.02%
.\.venv\Scripts\python.exe smc\run_smc.py --fee 0 --slip 0      # edge bruto sin costes
.\.venv\Scripts\python.exe smc\run_smc.py --set sl_min_atr=0.8 min_rr=2.5
```

Aproximaciones conocidas (pendiente de validar contra una lista de operaciones exportada de TradingView)
- Pivotes con empate permitido. ATR/EMA sin semilla SMA.
- `request.security(..., f()[1], lookahead_off)`: `htf_mode=tv` asume que el `[1]` anade un retraso extra
  (la vela HTF cerrada mas el desplazamiento). `htf_mode=simple` usa solo el `[1]`. Los resultados son casi iguales.
- Datos: volumen de Binance Futures (puede diferir del de TradingView).
- No modela liquidacion, funding ni minimos de orden de Binance.
