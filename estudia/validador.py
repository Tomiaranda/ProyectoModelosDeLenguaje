"""Validador del JSON de estud.ia.

Revisa estructura, tipos y reglas de cada pregunta. Devuelve la lista de
errores en castellano para que el modelo los corrija en un reintento. Antes de
validar normaliza descuidos inofensivos (un índice suelto en vez de lista, un
"true" entre comillas) para no gastar un reintento en ellos.
"""
import json
import re

from estudia.formato import TIPOS

HUECO = "___"


def extraer_json(texto: str) -> dict:
    """Saca el objeto JSON de la respuesta del modelo (tolera ```json ... ``` o texto alrededor)."""
    texto = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto.strip())
    inicio, fin = texto.find("{"), texto.rfind("}")
    if inicio == -1 or fin == -1:
        raise ValueError("la respuesta no contiene un objeto JSON")
    return json.loads(texto[inicio:fin + 1])


def _normalizar(p: dict) -> None:
    if isinstance(p.get("correctas"), int):
        p["correctas"] = [p["correctas"]]
    if isinstance(p.get("respuesta"), str) and p["respuesta"].lower() in {"true", "false"}:
        p["respuesta"] = p["respuesta"].lower() == "true"
    if p.get("tipo") == "completar" and isinstance(p.get("respuestas"), list):
        p["respuestas"] = [r if isinstance(r, list) else [r] for r in p["respuestas"]]


def _lista_de_textos(valor, minimo: int = 1) -> bool:
    return (isinstance(valor, list) and len(valor) >= minimo
            and all(isinstance(v, str) and v.strip() for v in valor))


def _validar_pregunta(p, n: int, tipos_permitidos: set[str]) -> list[str]:
    if not isinstance(p, dict):
        return [f"Pregunta {n}: tiene que ser un objeto."]
    _normalizar(p)
    tipo = p.get("tipo")
    e = []
    if tipo not in TIPOS:
        return [f'Pregunta {n}: tipo «{tipo}» inválido. Opciones: {", ".join(TIPOS)}.']
    if tipo not in tipos_permitidos:
        e.append(f'Pregunta {n}: el alumno no pidió preguntas de tipo «{tipo}».')
    if not isinstance(p.get("consigna"), str) or not p["consigna"].strip():
        e.append(f"Pregunta {n}: falta la «consigna».")
    if not isinstance(p.get("explicacion"), str) or not p["explicacion"].strip():
        e.append(f"Pregunta {n}: falta la «explicacion».")

    if tipo in {"multiple_choice", "seleccion_multiple"}:
        opciones, correctas = p.get("opciones"), p.get("correctas")
        if not _lista_de_textos(opciones, 2):
            e.append(f"Pregunta {n}: «opciones» debe ser una lista de al menos 2 textos.")
        elif len(set(opciones)) != len(opciones):
            e.append(f"Pregunta {n}: hay opciones repetidas.")
        if not (isinstance(correctas, list) and correctas
                and all(isinstance(i, int) and not isinstance(i, bool) for i in correctas)):
            e.append(f"Pregunta {n}: «correctas» debe ser una lista de índices enteros.")
        elif isinstance(opciones, list):
            if any(i < 0 or i >= len(opciones) for i in correctas):
                e.append(f"Pregunta {n}: hay un índice en «correctas» fuera de rango "
                         f"(van de 0 a {len(opciones) - 1}).")
            if len(set(correctas)) != len(correctas):
                e.append(f"Pregunta {n}: «correctas» tiene índices repetidos.")
        if tipo == "multiple_choice" and isinstance(correctas, list) and len(correctas) != 1:
            e.append(f"Pregunta {n}: multiple_choice lleva UN solo índice en «correctas».")

    elif tipo == "verdadero_falso":
        if not isinstance(p.get("respuesta"), bool):
            e.append(f"Pregunta {n}: «respuesta» debe ser true o false (booleano).")

    elif tipo == "completar":
        huecos = p.get("consigna", "").count(HUECO) if isinstance(p.get("consigna"), str) else 0
        respuestas = p.get("respuestas")
        if huecos == 0:
            e.append(f"Pregunta {n}: la consigna de «completar» necesita huecos ___.")
        if not (isinstance(respuestas, list) and all(_lista_de_textos(r) for r in respuestas)):
            e.append(f"Pregunta {n}: «respuestas» debe ser una lista de listas de textos.")
        elif len(respuestas) != huecos:
            e.append(f"Pregunta {n}: hay {huecos} huecos ___ pero {len(respuestas)} listas en «respuestas».")

    elif tipo == "texto":
        if not _lista_de_textos(p.get("respuestas")):
            e.append(f"Pregunta {n}: «respuestas» debe ser una lista de respuestas modelo.")

    elif tipo == "unir":
        pares = p.get("pares")
        if not (isinstance(pares, list) and 2 <= len(pares) <= 10
                and all(_lista_de_textos(par, 2) and len(par) == 2 for par in pares)):
            e.append(f'Pregunta {n}: «pares» debe tener de 2 a 10 pares ["izquierda", "derecha"].')
        elif len({a for a, _ in pares}) != len(pares) or len({b for _, b in pares}) != len(pares):
            e.append(f"Pregunta {n}: en «unir» no puede haber elementos repetidos en una columna.")

    elif tipo == "ordenar":
        pasos = p.get("pasos")
        if not (_lista_de_textos(pasos, 2) and len(pasos) <= 12):
            e.append(f"Pregunta {n}: «pasos» debe tener de 2 a 12 textos.")
        elif len(set(pasos)) != len(pasos):
            e.append(f"Pregunta {n}: «pasos» tiene elementos repetidos.")
    return e


def validar_banco(banco, cantidad: int, tipos: list[str] | None = None) -> list[str]:
    """Lista de errores (vacía si el banco es válido para estud.ia)."""
    if not isinstance(banco, dict):
        return ["La raíz tiene que ser un objeto con titulo, descripcion y preguntas."]
    errores = []
    if not isinstance(banco.get("titulo"), str) or not banco["titulo"].strip():
        errores.append("Falta «titulo».")
    if "descripcion" in banco and not isinstance(banco["descripcion"], str):
        errores.append("«descripcion» debe ser texto.")
    preguntas = banco.get("preguntas")
    if not isinstance(preguntas, list) or not preguntas:
        return errores + ["«preguntas» debe ser una lista no vacía."]
    if len(preguntas) != cantidad:
        errores.append(f"Se pidieron {cantidad} preguntas y hay {len(preguntas)}.")
    permitidos = set(tipos or TIPOS)
    for n, p in enumerate(preguntas, 1):
        errores.extend(_validar_pregunta(p, n, permitidos))
    consignas = [p.get("consigna", "").strip().lower() for p in preguntas if isinstance(p, dict)]
    if len(set(consignas)) != len(consignas):
        errores.append("Hay preguntas repetidas (misma consigna).")
    return errores
