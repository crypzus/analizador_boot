# Contexto para continuar (desde la propuesta del agente de IA en adelante)

Fecha: 2026-11-06. Carpeta del proyecto: `C:\Users\DELL\Documents\ernestor\programacion\trading-bot`
Entorno Python: `.venv` dentro de esa carpeta (Windows, PowerShell). Idioma de trabajo: espanol.
Nota: no es asesoria financiera. Nada de lo de abajo opera ni usa claves de trading.

## 1. La propuesta
Crear un agente que "aprenda" conceptos de trading, lea velas de Binance (4h ultimos 2 meses, 1h ultimos 15 dias,
15m ultimas 24 h) mas noticias cripto, y dé predicciones del comportamiento del precio en las proximas 24 h.

### Lo que se respondio
- Es factible tecnicamente. "Aprender" = darle al modelo (Claude) las reglas escritas (tus conceptos SMC, etc.), no entrenarlo.
- Datos de precio: la API publica de Binance no requiere key (~360 velas 4h, ~360 de 1h, 96 de 15m). Pre-calcular indicadores
  y niveles en codigo y pasar un resumen al modelo (los LLM leen mal miles de numeros crudos).
- Salida deseable: escenarios con probabilidades, niveles clave y que invalidaria cada escenario.
- Lo dificil NO es construirlo sino demostrar que acierta mas que el azar: predicciones a 24 h son muy ruidosas.
  Un backtest historico con LLM no vale (el modelo "conoce" el pasado). Solo sirve una prueba en vivo hacia adelante:
  guardar cada prediccion con marca de tiempo, compararla 24 h despues, medir acierto y Brier score frente a referencias
  simples, y esperar >= 100 predicciones antes de concluir.
- Si algun dia se conecta una API key de Binance: solo lectura, sin retiros, restringida por IP. No dejar que opere sin evidencia.
- Recomendacion: prototipo de solo analisis + registro de predicciones, sin operar.

## 2. Vigilar X (Trump, SEC, Hayes, CZ, Musk)
- Claude no se conecta a X por si solo: hay que construir un script que lea posts y los pase a Claude.
- API de X (segun blogs de terceros, a confirmar en docs.x.com): desde feb-2026 es de pago por uso, ~US$0,005 por post leido,
  tope mensual de lecturas, plan gratuito eliminado. Estimacion propia: ~15-30 US$/mes para 5 cuentas (no confirmada).
- Scraping de X rompe sus terminos: no se recomienda.
- Trump publica sobre todo en Truth Social, no en X. La SEC publica en sec.gov. Hayes, CZ y Musk solo estan en X (sin via gratuita).
- Las publicaciones son texto no confiable (posible manipulacion/cuentas falsas): el sistema solo debe alertar, nunca operar.

## 3. Fuentes gratuitas verificadas (sin cuenta)
- SEC: `https://www.sec.gov/news/pressreleases.rss` (otros feeds de la SEC piden un User-Agent con correo de contacto).
- Trump: API publica de Truth Social (cuenta id 107780257626128497) y espejo `https://www.trumpstruth.org/feed` como plan B.
- Medios con RSS que respondieron: CoinDesk, Cointelegraph, The Block (los tres con texto completo o casi), Decrypt, Bitcoin Magazine, CryptoSlate.
- Fed (RSS) funciona de forma irregular; se trata como opcional. NO se debe disfrazar el cliente como navegador (eso es evadir bloqueo de bots).
- Binance: anuncios por su API CMS publica; datos de mercado por fapi (funding, open interest, long/short). Fear & Greed: alternative.me.
- Calendario macro: nfs.faireconomy.media (limita consultas, error 429 si se llama muy seguido).
- Cobertura estimada de la "capa de noticias" (juicio subjetivo): SEC+Trump ~25%; + medios ~45%; + Binance/macro/mercado ~60-70%.
  Mas cobertura no implica mas ventaja: el precio suele descontar la noticia antes de que llegue la alerta.

## 4. Monitor construido: `monitor\`
Archivos: run.py, sources.py, rules.py, store.py, test_monitor.py, README.md. Salidas en `monitor\out\` (alerts.md, events.jsonl, monitor.db, digest.md).
- Ciclo cada 8 min (+/-20 s). Primera ejecucion solo registra lo existente sin avisar.
- Prioridad HIGH/MEDIUM/LOW por palabras clave ponderadas (sin IA). Agrupa noticias repetidas entre medios distintos.
- Alertas de mercado (funding, open interest, movimiento ~1h, long/short frente a su percentil de 7 dias, Fear & Greed) y de macro (60 y 15 min antes).
- Pruebas: `.\.venv\Scripts\python.exe monitor\test_monitor.py` (6 pruebas, todas pasan). 3 ciclos reales verificados.
- Correcciones ya hechas: SEC no cripto -> LOW; tramites rutinarios de la Fed -> LOW; anuncios de Binance no se agrupan;
  incidentes de seguridad de Binance -> HIGH (el feed mostraba avisos "Security Incident | Recovery Update"; detalles sin verificar).
- No esta corriendo ningun proceso. Para iniciarlo: `.\.venv\Scripts\python.exe monitor\run.py` (Ctrl+C para parar).

## 5. Pendiente / siguientes pasos posibles
1. Decidir si dejar el monitor corriendo en bucle.
2. Opcional: paso de IA (API de Claude, clave distinta de la suscripcion) que resuma/clasifique alertas.
3. Prototipo del agente de predicciones a 24 h con registro y evaluador (Brier, comparacion con referencias), solo analisis.
4. Opcional: aviso a Telegram (variables TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID).
5. Opcional mas adelante: API de X de pago por uso (con tope de gasto) para Hayes, CZ y Musk.

## 6. Trabajo anterior (NO es parte de este contexto, solo para saber que existe)
En la misma carpeta hay backtests de estrategias de trading (Freqtrade, port de un Pine SMC, tendencia diaria, torneo spot) que
no mostraron ventaja demostrada. No hace falta releerlos para este tema.
