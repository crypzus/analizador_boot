"""Exporta el informe en dos mensajes de texto a tu Telegram, para pegarlos en una conversacion con Claude.

  python reporter/export.py

Hace una corrida fresca (queda guardada la prediccion del predictor base en el journal, que luego usa
save_claude.py) y envia: 1) DATOS TECNICOS y 2) NOTICIAS. No usa la API de Claude ni cuesta nada.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import notify  # noqa: E402
import run  # noqa: E402

OUT = Path(__file__).resolve().parent / "out"
NEWS_MARK = "## Noticias y alertas"
PRED_MARK = "## Predicciones"
CHUNK = 3500  # margen bajo el limite de 4000 de notify.send


def split_report(text: str) -> tuple[str, str]:
    """(tecnico, noticias). Las predicciones del predictor base van con el tecnico."""
    i = text.find(NEWS_MARK)
    if i < 0:
        return text.strip(), ""
    j = text.find(PRED_MARK, i)
    news = text[i:j] if j >= 0 else text[i:]
    tech = text[:i] + (text[j:] if j >= 0 else "")
    return tech.strip(), news.strip()


def chunks(text: str, limit: int = CHUNK) -> list[str]:
    """Parte por lineas para que ningun mensaje pase el limite de Telegram."""
    parts, cur = [], ""
    for line in text.splitlines():
        line = line[:limit]
        if cur and len(cur) + len(line) + 1 > limit:
            parts.append(cur)
            cur = ""
        cur = f"{cur}\n{line}" if cur else line
    if cur:
        parts.append(cur)
    return parts


def export(out_dir=OUT, send=notify.send, run_fn=None) -> dict:
    out_dir = Path(out_dir)
    res = (run_fn or (lambda: run.run_once(out_dir, send=lambda t: False)))()
    if res.get("skipped") or not res.get("ok"):
        return {"sent": 0, "error": "no se pudo generar el informe (otra corrida en curso o sin datos)"}
    tech, news = split_report((out_dir / "report.md").read_text(encoding="utf-8"))
    sent = total = 0
    for title, body in (("📋 DATOS TÉCNICOS", tech), ("📰 NOTICIAS", news)):
        parts = chunks(body)
        for n, part in enumerate(parts, 1):
            total += 1
            tag = f" ({n}/{len(parts)})" if len(parts) > 1 else ""
            sent += bool(send(f"{title}{tag}\n\n{part}"))
    return {"sent": sent, "total": total}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(export())
