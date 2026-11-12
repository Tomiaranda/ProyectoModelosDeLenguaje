"""Prompt oficial del formato de estud.ia (https://estud-ia-blond.vercel.app/).

Es el texto que la plataforma pide para convertir material en un banco de
preguntas. La tool generadora lo completa con la cantidad, los tipos pedidos y
el material recopilado (libro + campus + web).
"""

TIPOS = (
    "multiple_choice", "seleccion_multiple", "verdadero_falso",
    "completar", "texto", "unir", "ordenar",
)

PROMPT_ESTUDIA = """Quiero que conviertas el material que te paso abajo en un banco de preguntas para estudiar, en formato JSON, para cargarlo en la página estud.ia.

Devolvé SOLO el JSON (sin texto antes ni después, sin comentarios), con esta estructura exacta:

{
  "titulo": "Nombre corto del test",
  "descripcion": "Descripción opcional",
  "preguntas": [ ... ]
}

Cada pregunta tiene "tipo", "consigna" y, opcionalmente, "explicacion" (se muestra después de responder). Los tipos posibles son:

1. "multiple_choice": una sola opción correcta.
   { "tipo": "multiple_choice", "consigna": "...", "opciones": ["A", "B", "C", "D"], "correctas": [1] }
   "correctas" tiene UN solo índice; los índices empiezan en 0 (la primera opción es 0).

2. "seleccion_multiple": una o más opciones correctas (el alumno tiene que marcar todas).
   { "tipo": "seleccion_multiple", "consigna": "...", "opciones": ["A", "B", "C", "D"], "correctas": [0, 2] }

3. "verdadero_falso":
   { "tipo": "verdadero_falso", "consigna": "Afirmación...", "respuesta": true }
   "respuesta" es true o false (booleano, sin comillas).

4. "completar": la consigna lleva uno o más huecos escritos como ___ (tres guiones bajos).
   { "tipo": "completar", "consigna": "La capital de Francia es ___.", "respuestas": [["París", "Paris"]] }
   "respuestas" tiene UNA lista por cada hueco, en orden, con todas las variantes aceptadas para ese hueco.

5. "texto": el alumno escribe la respuesta libremente (la corrige una IA comparándola con las respuestas correctas).
   { "tipo": "texto", "consigna": "...", "respuestas": ["Respuesta modelo", "Otra forma válida"] }

6. "unir": el alumno une con flechas cada elemento de la izquierda con su pareja de la derecha (de 2 a 10 pares).
   { "tipo": "unir", "consigna": "Uní cada país con su capital.", "pares": [["Francia", "París"], ["Italia", "Roma"], ["Japón", "Tokio"]] }
   Cada par es ["izquierda", "derecha"] y está correcto así: la página mezcla la columna derecha sola.

7. "ordenar": el alumno ordena pasos o hechos (de 2 a 12).
   { "tipo": "ordenar", "consigna": "Ordená las etapas de la mitosis.", "pasos": ["Profase", "Metafase", "Anafase", "Telofase"] }
   Escribí los "pasos" en el orden CORRECTO: la página los desordena sola.

Reglas:
- Usá comillas dobles, sin comas finales y sin comentarios: tiene que ser JSON válido.
- {regla_tipos}
- Incluí siempre "explicacion" justificando la respuesta según el material de la cátedra.
- Las opciones incorrectas tienen que ser creíbles, no obvias.
- No repitas preguntas.
- Generá {cantidad} preguntas.
{reglas_extra}
Material:
{material}"""

SISTEMA_GENERADOR = (
    "Sos un docente de la UTN FRM que arma bancos de preguntas de práctica. "
    "Respondés únicamente con JSON válido, sin bloques de código ni texto adicional. "
    "Todo el contenido de las preguntas sale del material provisto: no inventes "
    "definiciones que contradigan la bibliografía de la cátedra."
)


def armar_prompt(cantidad: int, tipos: list[str] | None, material: str,
                 titulo: str, temas: str, indicaciones: str = "") -> str:
    if tipos:
        regla_tipos = (
            "Usá SOLO estos tipos de pregunta, pedidos por el alumno: "
            + ", ".join(f'"{t}"' for t in tipos) + "."
        )
    else:
        regla_tipos = ("Mezclá los tipos de pregunta y cubrí todos los temas "
                       "importantes del material.")
    extra = [
        f'- Usá como "titulo" exactamente: "{titulo}".',
        f"- Las preguntas tienen que cubrir estos temas: {temas}.",
        "- En cada \"explicacion\" citá la fuente entre paréntesis, por ejemplo "
        "(Libro: <archivo>, págs. 12-13) o (Campus: <archivo>).",
    ]
    if indicaciones:
        extra.append(f"- Indicaciones del alumno: {indicaciones}")
    return (
        PROMPT_ESTUDIA
        .replace("{regla_tipos}", regla_tipos)
        .replace("{cantidad}", str(cantidad))
        .replace("{reglas_extra}", "\n".join(extra) + "\n")
        .replace("{material}", material)
    )
