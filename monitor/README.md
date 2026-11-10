# Monitor de noticias y mercado (solo lee y avisa)

No opera, no usa claves de trading, no necesita cuentas. Todo el contenido externo se trata como texto no confiable.

## Fuentes (todas gratuitas, sin cuenta)
| Tipo | Fuente | Frecuencia |
|---|---|---|
| Regulacion | SEC comunicados (RSS) | cada ciclo |
| Politica | Trump en Truth Social (API publica; plan B: espejo trumpstruth.org) | cada ciclo |
| Banco central | Fed comunicados (RSS, opcional: puede rechazar clientes automaticos) | cada 60 min |
| Medios | CoinDesk, Cointelegraph, The Block, Decrypt, Bitcoin Magazine, CryptoSlate | cada ciclo |
| Exchange | Anuncios de Binance: listados, retiradas, noticias | cada ciclo |
| Macro | Calendario USD de alto impacto (faireconomy). Avisa 60 y 15 min antes | cada 60 min |
| Mercado | BTC, ETH, SOL, ADA: precio, funding, open interest, long/short (Binance Futures), Fear & Greed | cada ciclo |

Ciclo = 8 minutos (+/- 20 s de variacion).

## Uso (PowerShell, dentro de `trading-bot`)
```powershell
.\.venv\Scripts\python.exe monitor\test_monitor.py     # pruebas sin red
.\.venv\Scripts\python.exe monitor\run.py --once       # un ciclo
.\.venv\Scripts\python.exe monitor\run.py              # bucle cada 8 min (Ctrl+C para parar)
.\.venv\Scripts\python.exe monitor\run.py --digest     # resumen de 24 h en monitor\out\digest.md
```
La primera ejecucion solo registra las noticias existentes (sin avisos) para no inundarte.
No lances varias copias a la vez: el calendario macro limita las consultas (error 429).

## Salidas (`monitor\out\`)
`alerts.md` (alertas legibles), `events.jsonl` (todo lo recibido con prioridad), `monitor.db` (SQLite), `digest.md`.

## Prioridad (reglas transparentes en `rules.py`, sin IA)
HIGH / MEDIUM / LOW por palabras clave ponderadas y por tipo de fuente. Una noticia LOW solo se guarda.
Las noticias repetidas entre medios se agrupan: se avisa una vez, y otra cuando una segunda fuente la confirma.
Las reglas se afinan con el uso (ya se corrigio: SEC no cripto -> LOW, tramites rutinarios de la Fed -> LOW, incidentes de seguridad de Binance -> HIGH).

## Opcional
- Avisos a Telegram: define `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` (tu propio bot de @BotFather).
- Un paso de IA (Claude) para resumir las alertas necesitaria una clave de la API de Claude; no esta incluido.
