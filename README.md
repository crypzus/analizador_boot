# Bot de trading SOL/ADA (Binance Futures) - entorno de trabajo

Estado: **solo dry-run / backtest**. Sin API keys.

## Estructura
- `referencias/`  Freqtrade y passivbot clonados (solo lectura, ignorados por git).
- `user_data/strategies/TrendEmaAtr.py`  nuestra estrategia (parametros a ajustar).
- `config.example.json`  copiar a `config.json` (ignorado por git). Telegram desactivado.

## Instalacion (PowerShell, en esta carpeta)
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install freqtrade
```

## Flujo
1. `freqtrade download-data -c config.json --timerange 20250101- -t 15m 1h --trading-mode futures`
2. `freqtrade backtesting -c config.json -s TrendEmaAtr --timerange 20250601-`
3. Ajuste: `freqtrade hyperopt` en un periodo y **validar en otro** (walk-forward), nunca en el mismo.
4. `freqtrade trade -c config.json -s TrendEmaAtr` con `dry_run: true` durante 3-4 semanas.
5. Solo entonces real, con $30-50 y 2-3x.

## Seguridad API key (Binance)
- Permisos: solo lectura + Futures. **Sin retiros. Sin transferencias.**
- Restringir por IP. Guardar la clave en `config.private.json` (ignorado por git), nunca en el repo.
- Telegram: `"enabled": false`. Se activa solo si tu mismo creas el bot con @BotFather.
