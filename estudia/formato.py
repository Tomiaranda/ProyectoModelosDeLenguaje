"""Tipos de pregunta del formato de estud.ia (https://estud-ia-blond.vercel.app/).

Las instrucciones del formato (estructura del JSON, campos de cada tipo y
reglas) viven en la skill `skills/generar-banco-estudia/SKILL.md`, que el
agente carga cuando tiene que escribir el banco. Acá solo queda la lista de
tipos que comparten la skill, la tool `guardar_banco_estudia` y el validador.
"""

TIPOS = (
    "multiple_choice", "seleccion_multiple", "verdadero_falso",
    "completar", "texto", "unir", "ordenar",
)