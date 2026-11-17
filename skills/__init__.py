"""Skills del agente: instrucciones especializadas que se cargan bajo demanda.

Cada skill es una carpeta `skills/<nombre>/SKILL.md` con un encabezado YAML
(`name` y `description`) y, debajo, las instrucciones en markdown. El system
prompt solo lista el nombre y la descripción de cada skill; el cuerpo completo
se le entrega al modelo cuando llama a la tool `cargar_skill` (divulgación
progresiva: no ocupa contexto hasta que hace falta).
"""
from dataclasses import dataclass
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Skill:
    nombre: str
    descripcion: str
    instrucciones: str
    ruta: Path


def _parsear(ruta: Path) -> Skill:
    """Lee un SKILL.md: encabezado `---` con `clave: valor` y el cuerpo en markdown."""
    texto = ruta.read_text(encoding="utf-8")
    meta: dict[str, str] = {}
    cuerpo = texto
    if texto.startswith("---"):
        _, encabezado, cuerpo = texto.split("---", 2)
        for linea in encabezado.strip().splitlines():
            clave, _, valor = linea.partition(":")
            meta[clave.strip()] = valor.strip()
    return Skill(
        nombre=meta.get("name") or ruta.parent.name,
        descripcion=meta.get("description", ""),
        instrucciones=cuerpo.strip(),
        ruta=ruta,
    )


def cargar_skills(directorio: Path = SKILLS_DIR) -> dict[str, Skill]:
    """Todas las skills de `directorio`, por nombre."""
    skills = (_parsear(r) for r in sorted(directorio.glob("*/SKILL.md")))
    return {s.nombre: s for s in skills}


def catalogo(skills: dict[str, Skill]) -> str:
    """Lista `- nombre: descripción` para el system prompt."""
    return "\n".join(f"- `{s.nombre}`: {s.descripcion}" for s in skills.values()) or "- ninguna"
