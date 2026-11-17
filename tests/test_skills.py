"""Pruebas del cargador de skills (no usan red ni API keys).

    python -m pytest tests
"""
from estudia.formato import TIPOS
from skills import cargar_skills, catalogo


def test_skill_generar_banco_disponible():
    skills = cargar_skills()
    skill = skills["generar-banco-estudia"]
    assert skill.descripcion
    assert "guardar_banco_estudia" in skill.instrucciones
    assert not skill.instrucciones.startswith("---")   # el encabezado no viaja al modelo


def test_skill_documenta_todos_los_tipos():
    instrucciones = cargar_skills()["generar-banco-estudia"].instrucciones
    for tipo in TIPOS:
        assert f'"{tipo}"' in instrucciones


def test_catalogo_lista_nombre_y_descripcion(tmp_path):
    carpeta = tmp_path / "demo"
    carpeta.mkdir()
    (carpeta / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Hace algo: de prueba\n---\n\n# Demo\nCuerpo.", encoding="utf-8")
    skills = cargar_skills(tmp_path)
    assert skills["demo"].instrucciones == "# Demo\nCuerpo."
    assert catalogo(skills) == "- `demo`: Hace algo: de prueba"
