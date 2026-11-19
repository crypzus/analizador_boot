"""Genera la skill `predice` (un solo archivo, autocontenido) y la empaqueta para claude.ai.

  python reporter/build_skill.py

SKILL.md = reporter/predice_skill_template.md + reporter/reglas.md incrustado (una sola fuente de verdad: edita
reglas.md o la plantilla, nunca el SKILL.md generado). No usa archivos auxiliares porque algunas instalaciones
(claude.ai, plugins) solo conservan SKILL.md y la skill se quedaba sin las reglas.

Valida: cabecera, que las reglas completas esten dentro, que el JSON de ejemplo lo acepte save_claude, y que el
paquete reporter/out/predice.skill contenga solo predice/SKILL.md.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SKILL = ROOT / ".claude" / "skills" / "predice"
OUT = HERE / "out"
sys.path.insert(0, str(HERE))
import setups  # noqa: E402


def demote(md: str) -> str:
    """Baja dos niveles los titulos de reglas.md para que cuelguen de 'Reglas completas'."""
    return "\n".join(("##" + ln) if ln.startswith("#") else ln for ln in md.splitlines())


def build_text() -> str:
    reglas = (HERE / "reglas.md").read_text(encoding="utf-8").strip()
    h = hashlib.sha256(reglas.encode("utf-8")).hexdigest()[:8]
    tpl = (HERE / "predice_skill_template.md").read_text(encoding="utf-8")
    return tpl.replace("{{HASH}}", h).replace("{{REGLAS}}", demote(reglas))


def check(text: str) -> dict:
    m = re.match(r"---\nname: (\S+)\ndescription: (.+?)\n---\n", text, re.S)
    if not m or m.group(1) != "predice":
        raise ValueError("cabecera de SKILL.md invalida")
    reglas = (HERE / "reglas.md").read_text(encoding="utf-8").strip().splitlines()
    body = text.replace("#", "")
    missing = [ln for ln in reglas if ln.strip() and ln.replace("#", "") not in body]
    if missing or "{{" in text:
        raise ValueError(f"las reglas no estan completas dentro de SKILL.md: {missing[:3]}")
    block = re.search(r"<<'EOF'\n(.*?)\nEOF", text, re.S)
    if not block:
        raise ValueError("falta el bloque de ejemplo preds.json")
    example = json.loads(block.group(1))
    for p in example.values():
        assert 0.05 <= p["prob_up"] <= 0.95 and p["note"]
        if p.get("setup"):
            setups.parse_setup(p["setup"])
    return example


def main() -> Path:
    text = build_text()
    ex = check(text)
    if SKILL.exists():
        shutil.rmtree(SKILL)                      # sin references/: la skill es un solo archivo
    SKILL.mkdir(parents=True)
    (SKILL / "SKILL.md").write_text(text, encoding="utf-8", newline="\n")
    OUT.mkdir(exist_ok=True)
    dest = OUT / "predice.skill"
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(SKILL / "SKILL.md", "predice/SKILL.md")
    with zipfile.ZipFile(dest) as z:
        assert z.namelist() == ["predice/SKILL.md"]
    print(f"ok: {len(text.splitlines())} lineas, reglas incluidas, ejemplo valido ({', '.join(ex)}); paquete {dest}")
    return dest


if __name__ == "__main__":
    main()
