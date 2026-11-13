# Reporter de mercado y predicciones a 24 h (solo analiza y avisa)

No opera, no usa claves de trading ni cuentas. No es asesoria financiera.
Pares: BTCUSDT, ETHUSDT, SOLUSDT, ADAUSDT. Velas publicas de Binance: 4h (60 dias), 1h (15 dias), 15m (24 h).

## Uso (PowerShell, dentro de `trading-bot`)
```powershell
.\.venv\Scripts\python.exe reporter\test_reporter.py   # pruebas sin red
.\.venv\Scripts\python.exe reporter\run.py --once      # una corrida
.\.venv\Scripts\python.exe reporter\run.py             # bucle cada 4 h (Ctrl+C para parar)
.\.venv\Scripts\python.exe reporter\run.py --stats     # Brier y acierto del journal
```
Telegram es opcional: define `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` (variables de entorno). Sin ellas solo escribe archivos.
Las noticias salen de `monitor\out\monitor.db` (solo lectura); si no existe, el informe lo indica y sigue.

## Salidas (`reporter\out\`)
`report.md` (ultimo informe), `journal.db` (predicciones y corridas), `predictions.jsonl`, `stats.md`.

## Como se evalua
Cada prediccion (prob. de subir en 24 h) se compara 24 h despues con el cierre de la vela 15m correspondiente.
Se reporta el Brier del modelo frente a dos referencias ("siempre 50 %" y "repite la direccion de las ultimas 24 h").
Las 4 monedas se mueven juntas: cuenta el numero de **corridas**, no de predicciones. No concluir con menos de ~100 evaluadas por moneda.

## Siguiente paso
Sustituir `baseline.py` por el agente con Claude (misma interfaz: resumen de la moneda -> `prob_up`, `invalidation`, `note`).

## Paso automatico con Claude (APAGADO por defecto)
`claude_agent.py` envia el informe (tecnico + noticias) y `reglas.md` a la API de Claude, valida el JSON y guarda las
predicciones como `claude_v1`, comparables con el predictor base. No se usa hasta que lo actives.
- Instalar: `.\.venv\Scripts\python.exe -m pip install anthropic`
- Clave: variable de entorno `ANTHROPIC_API_KEY` (nunca en el codigo ni en el chat).
- Activar: `run.py --once --claude` o `REPORTER_USE_CLAUDE=1`.
- Modelo: `claude-opus-5-5` (cambiar con `REPORTER_CLAUDE_MODEL`, por ejemplo `claude-sonnet-5-5` para gastar menos).
- Tope de gasto: maximo 8 corridas con Claude cada 24 h (`REPORTER_CLAUDE_MAX_PER_DAY`).
- Si la API falla o la respuesta no es valida, no se guarda nada y la corrida sigue. Las noticias se tratan como datos, no como ordenes.
- Sin probar contra la API real (no hay clave todavia): la primera vez, correrlo con `--once --claude` y mirar la salida `[claude] ...`.

## En un VPS (24/7)
Linux + Python + `pip install requests pandas numpy anthropic`. Region fuera de EE. UU. (Binance bloquea esas IPs).
Variables (`TELEGRAM_*`, `ANTHROPIC_API_KEY`) en un archivo solo legible por el usuario, cargado por el servicio.
Lo mas robusto: un temporizador systemd que ejecute `reporter/run.py --once` cada 4 h, y el monitor como servicio aparte.
