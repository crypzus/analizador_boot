# Reporter de mercado y predicciones a 24 h — Diseño

Fecha: 2026-11-09. Carpeta nueva: `reporter\` (junto a `monitor\`, que no se modifica).
Aviso: no es asesoría financiera. Solo analiza y avisa. No opera ni usa claves de trading.

## 1. Objetivo
Cada 4 h, para BTCUSDT, ETHUSDT, SOLUSDT y ADAUSDT:
1. Bajar velas públicas de Binance y calcular indicadores y niveles.
2. Armar un informe de mercado en texto (incluye noticias y alertas de las últimas 24 h del monitor).
3. Generar una predicción por moneda (probabilidad de que el precio suba en 24 h) con un predictor base sin IA.
4. Guardar cada predicción con marca de tiempo y evaluarla 24 h después.
5. Enviar un solo mensaje a Telegram con las 4 monedas.

**Criterio de éxito:** tras acumular al menos 100 predicciones evaluadas por moneda, comparar el Brier score del predictor
frente a dos referencias (siempre 50 % y "repite la dirección de las últimas 24 h"). Sin esa evidencia no se concluye nada.

## 2. Fuera de alcance
- Llamar a Claude (se conecta después reemplazando solo `baseline.py`, con el mismo formato de entrada y salida).
- Operar, leer cuentas, usar claves de Binance, backtests con LLM.
- X/Twitter, otros pares, despliegue en VPS (el código debe poder mudarse, pero no se despliega aquí).

## 3. Componentes (cada uno con un solo propósito)
| Archivo | Qué hace | Depende de |
|---|---|---|
| `candles.py` | Descarga velas 4h (60 días), 1h (15 días), 15m (24 h) por par. Valida (sin huecos, orden, tipos) y reintenta con espera. Usa `data-api.binance.vision` como plan B si `api.binance.com` responde 451. | requests |
| `indicators.py` | EMA 20/50, RSI 14, ATR 14, volumen relativo, máx/mín por marco, tendencia por marco, soportes y resistencias por swings. Función pura: velas entran, números salen. | pandas, numpy |
| `report.py` | Arma el informe de texto: un bloque por moneda + correlación con BTC + noticias/alertas de 24 h leídas de `monitor\out\monitor.db` en solo lectura (si no existe, lo indica y sigue). | indicators, sqlite3 |
| `baseline.py` | Predictor base sin IA: probabilidad de subir según tendencia 4h y RSI. También calcula las dos referencias. Interfaz: informe de la moneda → `{prob_up, nivel_invalidacion, nota}`. | indicators |
| `journal.py` | SQLite `reporter\out\journal.db`: guarda predicciones (corrida, par, hora, precio, prob, método), evalúa las vencidas a 24 h con el precio real, calcula acierto y Brier por moneda y global, y registra las corridas realizadas (para detectar huecos). | sqlite3 |
| `notify.py` | Envía el mensaje a Telegram con `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` del entorno. Si faltan, solo escribe en `reporter\out\`. Nunca imprime el token. | requests |
| `run.py` | Orquestador: `--once` (una corrida) o bucle cada 4 h; evalúa predicciones vencidas; `--stats` muestra Brier y acierto. | todos |

## 4. Flujo de datos
`candles → indicators → report (+ monitor.db) → baseline → journal (guarda) → notify (Telegram)`; al inicio de cada corrida
`journal` evalúa las predicciones con más de 24 h.

## 5. Salidas (`reporter\out\`)
`report.md` (último informe), `journal.db`, `predictions.jsonl`, `stats.md`.

## 6. Errores y límites
- Una moneda que falla no frena a las demás: se marca "sin datos" en el informe y no se guarda predicción para ella.
- Si Binance no responde tras los reintentos, la corrida se registra como fallida (no se inventan datos).
- Texto de noticias del monitor se trata como no confiable: solo se muestra, nunca se ejecuta ni se interpreta como instrucción.
- Un solo proceso a la vez (archivo de bloqueo en `out\`).

## 7. Evaluación honesta
- Las 4 monedas se mueven juntas: se cuentan **corridas**, no predicciones, como tamaño de muestra; el Brier se reporta por moneda y global.
- El journal anota las corridas fallidas o saltadas, para que los huecos no sesguen la estadística.
- La evaluación usa el precio de cierre de la vela 15m más cercana a +24 h.

## 8. Pruebas
Sin red, con velas de ejemplo: indicadores (valores conocidos), Brier y evaluación del journal, formato del informe, manejo de
moneda fallida. Luego una corrida real con `--once` verificando los archivos de salida y el mensaje de Telegram.

## 9. Ejecución
Local por ahora: `.\.venv\Scripts\python.exe reporter\run.py --once`. Sin rutas fijas de máquina: todo relativo a la carpeta
del proyecto, para poder copiarlo a un VPS después.
