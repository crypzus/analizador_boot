"""Sincroniza la skill `predice` con reporter/reglas.md y la empaqueta para claude.ai.

  python reporter/build_skill.py

1. Copia reglas.md a .claude/skills/predice/references/reglas.md (una sola fuente de verdad: edita reglas.md, no la copia).
2. Valida la cabecera de SKILL.md y que el JSON de ejemplo sea aceptado por save_claude.
3. Crea reporter/out/predice.skill (un .zip con la carpeta `predice/`) para subirlo en claude.ai > Ajustes > Capacidades.
"""
from __future__ import annotations

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


def check(skill_md: Path) -> dict:
    text = skill_md.read_text(encoding="utf-8")
    m = re.match(r"---\nname: (\S+)\ndescription: (.+?)\n---\n", text, re.S)
    if not m or m.group(1) != "predice":
        raise ValueError("cabecera de SKILL.md invalida")
    block = re.search(r"<<'EOF'\n(.*?)\nEOF", text, re.S)
    if not block:
        raise ValueError("falta el bloque de ejemplo preds.json")
    example = json.loads(block.group(1))
    for sym, p in example.items():
        assert 0.05 <= p["prob_up"] <= 0.95 and p["note"]
        if p.get("setup"):
            setups.parse_setup(p["setup"])      # ValueError si el orden es incoherente
    return example


def main() -> Path:
    (SKILL / "references").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(HERE / "reglas.md", SKILL / "references" / "reglas.md")
    ex = check(SKILL / "SKILL.md")
    OUT.mkdir(exist_ok=True)
    dest = OUT / "predice.skill"
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(SKILL.rglob("*")):
            if f.is_file():
                z.write(f, Path("predice") / f.relative_to(SKILL))
    print(f"ok: ejemplo valido ({', '.join(ex)}); paquete en {dest}")
    return dest


if __name__ == "__main__":
    main()
