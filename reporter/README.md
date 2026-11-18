# Reporter de mercado y predicciones a 24 h (solo analiza y avisa)

No opera, no usa claves de trading ni cuentas. No es asesoria financiera.
Pares: BTCUSDT y ADAUSDT. Velas publicas de Binance: 4h (60 dias), 1h (15 dias), 15m (24 h).

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

## Exportar para analizar con Claude (gratis, sin API)
`python reporter/export.py` hace una corrida fresca y manda a Telegram dos mensajes de texto: `📋 DATOS TÉCNICOS` y
`📰 NOTICIAS` (partidos en trozos si son largos). Se pegan en una conversacion con Claude, que devuelve el JSON de
predicciones; luego `python reporter/save_claude.py preds.json` lo guarda como `claude_v1`.
En Termux se puede poner un boton en la pantalla de inicio con Termux:Widget (script en `~/.shortcuts/`).

## Setups de estudio (papel, sin dinero real)
Cada prediccion de Claude puede llevar un `setup` (`side`, `entry`, `stop`, `tp1`, `tp2`; ver `reglas.md`, seccion 6).
`save_claude.py` lo guarda en la tabla `setups` y en cada corrida (`run.py` o `export.py`) se evalua con velas 15m
posteriores al momento de guardarlo: entrada limite (caduca a las 24 h), 100 $ x5, mitad en TP1 y mitad en TP2,
cierre forzado a las 72 h, comision 0,05 % por lado. Si una vela toca stop y objetivo a la vez, gana el stop
(conservador). El resultado sale en `out/stats.md` (seccion "Setups de estudio"): operados, ganadores, stops, TP2, P&L.
Reglas completas en el docstring de `setups.py`.

## Pronostico de volatilidad (validado en `research/`)
Cada corrida estima el rango de las proximas 24 h de cada moneda (`volatility.py` + `vol_model.json`) y lo muestra en el informe y
en el mensaje de Telegram junto con un stop sugerido (30 % del rango esperado, heuristica). Es cuanto se mueve, no hacia donde.
El pronostico se guarda en el journal y a las 24 h se compara con el rango real (seccion "Pronostico de volatilidad" de `stats.md`).
Necesita 800 velas de 1h por moneda; si falta el modelo o los datos, el reporter funciona igual sin esa linea.
Reentrenar con datos nuevos: `python research/fit_vol_model.py` (solo escribe el modelo si pasa el criterio de validacion).

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
