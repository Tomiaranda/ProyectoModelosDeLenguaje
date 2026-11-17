"""Las tools del agente y su estado.

Orden de uso que impone el system prompt (ver agente.py):
    1. buscar_bibliografia   RAG sobre los libros oficiales de la cátedra (SQLite)
    2. explorar_campus       Campus Virtual UTN FRM vía MCP
    3. buscar_en_internet    DuckDuckGo, opcional, sin API key
    4. cargar_skill          carga la skill `generar-banco-estudia` (skills/)
       guardar_banco_estudia valida y guarda el JSON que escribió el agente (cierre)

Las tres primeras guardan lo que encuentran en el estado del agente
(`material`). La generación del banco ya no es una tool que llama a otro LLM:
es una skill (instrucciones en `skills/generar-banco-estudia/SKILL.md`) que el
propio agente carga y sigue para escribir el JSON. `guardar_banco_estudia`
hace cumplir en código lo que antes hacía la tool generadora: exige que se haya
consultado la bibliografía, valida el JSON, limita los reintentos y lo guarda.

Todas atrapan sus errores y los devuelven como texto: un fallo nunca corta el
flujo del agente, el modelo lee el error y decide cómo seguir.
"""
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from langchain.agents import AgentState
from langchain.messages import ToolMessage
from langchain.tools import ToolRuntime, tool
from langgraph.types import Command
from pydantic import BaseModel, Field

from campus.archivos import extraer_texto
from campus.cliente_mcp import CampusNoConfigurado, campus
from estudia.validador import extraer_json, validar_banco
from llm import obtener_embeddings
from rag import store
from skills import cargar_skills

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# Presupuesto de contexto. Llama 3.3 70B tiene 128k tokens, pero cada token
# cuenta contra los límites del tier gratuito: lo recuperado se recorta.
MAX_CHARS_FRAGMENTO_LIBRO = 2200   # ~550 tokens por fragmento de 2 páginas
MAX_CHARS_CAMPUS = 5000            # contenido de una materia o un archivo
MAX_CHARS_WEB = 600                # por resultado
MAX_CHARS_MATERIAL = 16000         # adjuntos que se entregan junto con la skill (~4k tokens)
INTENTOS_GENERACION = 3            # llamadas a guardar_banco_estudia por turno
SKILL_BANCO = "generar-banco-estudia"

TipoPregunta = Literal[
    "multiple_choice", "seleccion_multiple", "verdadero_falso",
    "completar", "texto", "unir", "ordenar",
]


# --- Estado y contexto -----------------------------------------------------------

@dataclass
class Contexto:
    """Datos de solo lectura de cada invocación (no se guardan en el estado)."""
    turno: int


def _acumular_material(actual: list | None, nuevo: list) -> list:
    """Reducer: agrega material sin repetir la misma fuente dentro de un turno."""
    por_clave = {(m["turno"], m["fuente"]): m for m in (actual or []) + nuevo}
    return list(por_clave.values())


class EstadoEstudio(AgentState):
    """Estado del agente: `messages` (heredado) + material recopilado por turno."""
    material: Annotated[list[dict], _acumular_material]


def _responder(runtime: ToolRuntime, texto: str, artifact: Any = None, **update) -> Command:
    """Devuelve el ToolMessage obligatorio y, opcionalmente, actualiza el estado.

    `artifact` viaja con el mensaje para la UI, pero no se le envía al modelo.
    """
    mensaje = ToolMessage(texto, tool_call_id=runtime.tool_call_id, artifact=artifact)
    return Command(update={**update, "messages": [mensaje]})


def _material(runtime: ToolRuntime, origen: str, fuente: str, texto: str) -> dict:
    return {"turno": runtime.context.turno, "origen": origen, "fuente": fuente, "texto": texto}


# --- 0. Plan (TO DO) ------------------------------------------------------------------

class Paso(BaseModel):
    paso: str = Field(description="Acción concreta, p. ej. «Buscar embeddings en la bibliografía».")
    estado: Literal["pendiente", "en_curso", "hecho"] = "pendiente"


@tool
def plan(pasos: list[Paso]) -> str:
    """Planificá como una lista TO DO los pedidos LARGOS: los que requieren varias fuentes
    (bibliografía + campus + leer archivos + internet) o varios temas. NO la uses para
    pedidos simples (cambios sobre un banco ya generado, pedir datos que faltan).

    Llamala al empezar con todos los pasos y el primero "en_curso". Después, cada vez que
    termines un paso, volvé a llamarla con la lista completa actualizada EN LA MISMA
    respuesta que la tool del paso siguiente (no gastes una llamada al modelo solo en esto).
    Podés agregar o sacar pasos si lo que encontrás cambia el plan.
    """
    hechos = sum(p.estado == "hecho" for p in pasos)
    return f"Plan actualizado: {hechos}/{len(pasos)} pasos hechos."


# --- 1. RAG de bibliografía oficial ------------------------------------------------

_db = store.conectar()
_embeddings = None


def _embeddings_rag():
    global _embeddings
    if _embeddings is None:
        _embeddings = obtener_embeddings()
    return _embeddings


def materias_con_bibliografia() -> dict[str, list[str]]:
    return store.materias_cargadas(_db)


@tool
def buscar_bibliografia(materia: str, consulta: str, runtime: ToolRuntime[Contexto],
                        cantidad: int = 3) -> Command:
    """PRIMER PASO OBLIGATORIO. Busca en los libros oficiales de la cátedra (RAG).

    Verifica si la materia tiene su bibliografía cargada y devuelve los fragmentos
    más relevantes (cada uno son 2 páginas de un libro) con el libro y las páginas
    de origen. Llamala una vez por cada tema pedido, con una consulta concreta
    (por ejemplo "atención multi-cabeza en transformers").

    Args:
        materia: nombre de la materia, p. ej. "Modelos de Lenguaje".
        consulta: tema o concepto a buscar, en castellano.
        cantidad: fragmentos a devolver (1 a 4, por defecto 3).
    """
    try:
        materia_db = store.resolver_materia(_db, materia)
        if materia_db is None:
            disponibles = ", ".join(materias_con_bibliografia()) or "ninguna"
            return _responder(
                runtime,
                f"No hay bibliografía oficial cargada para «{materia}». Materias con libros "
                f"indexados: {disponibles}. Si la materia es otra, avisale al alumno y seguí "
                "con el campus como fuente principal.",
                artifact={"materia": materia, "fragmentos": []},
            )
        vector = _embeddings_rag().embed_query(consulta)
        resultados = store.buscar(_db, materia_db, vector, k=max(1, min(cantidad, 4)))
    except Exception as e:   # noqa: BLE001 - cualquier fallo se le informa al modelo
        return _responder(runtime, f"Error al consultar la bibliografía: {e}")

    bloques, material = [], []
    for i, r in enumerate(resultados, 1):
        cita = f"{r['libro']}, págs. {r['pag_desde']}-{r['pag_hasta']}"
        texto = r["texto"][:MAX_CHARS_FRAGMENTO_LIBRO]
        bloques.append(f"[{i}] Libro: {cita}\n{texto}")
        material.append(_material(runtime, "libro", f"Libro: {cita}", texto))
    reporte = {
        "materia": materia_db,
        "fragmentos": [
            {"libro": r["libro"], "paginas": f"{r['pag_desde']}-{r['pag_hasta']}",
             "similitud": round(r["similitud"], 3),
             "vista": r["texto"][:160].replace("\n", " ")}
            for r in resultados
        ],
    }
    return _responder(runtime, "\n\n".join(bloques) or "Sin resultados.",
                      artifact=reporte, material=material)


# --- 2. Campus Virtual UTN FRM (MCP) -------------------------------------------------

@tool
def explorar_campus(accion: Literal["materias", "contenido", "leer"],
                    runtime: ToolRuntime[Contexto],
                    course_id: int | None = None, url: str | None = None,
                    desde: int = 0) -> Command:
    """SEGUNDO PASO. Explora el Campus Virtual UTN FRM (Moodle) mediante su servidor MCP.

    Sirve para cruzar la teoría del libro con el enfoque, los ejercicios y el
    recorte de temas de los docentes. Flujo: "materias" -> course_id ->
    "contenido" -> url de un apunte, filmina o guía -> "leer".
    Leé solo ítems [resource] (PDF, filminas, guías) o [page]: los [url] son
    links externos (Colab, YouTube) y no se pueden leer. Si el tema pedido está más
    adelante en el archivo, seguí leyendo con `desde` en vez de quedarte con el inicio.

    Args:
        accion: "materias" lista las materias del alumno con su course_id;
            "contenido" muestra unidades y archivos de una materia (requiere course_id);
            "leer" abre una URL del campus y devuelve el texto de la página o del
            archivo (PDF, PPTX, DOCX) (requiere url).
        course_id: id de la materia, sale de accion="materias".
        url: URL del campus, sale de accion="contenido".
        desde: con accion="leer", carácter desde el que seguir leyendo. Cada lectura
            devuelve un tramo; si el archivo sigue, el texto termina indicando el
            `desde` de la próxima llamada.
    """
    try:
        if accion == "materias":
            texto = campus.llamar("materias")
            return _responder(runtime, texto or "El alumno no tiene materias.",
                              artifact={"accion": accion})
        if accion == "contenido":
            if not course_id:
                return _responder(runtime, "Falta course_id: primero usá accion='materias'.")
            texto = campus.llamar("contenido_materia", {"course_id": course_id})[:MAX_CHARS_CAMPUS]
            material = [_material(runtime, "campus", f"Campus: programa del curso {course_id}", texto)]
            return _responder(runtime, texto, artifact={"accion": accion}, material=material)
        if not url:
            return _responder(runtime, "Falta url: sacala de accion='contenido'.")
        respuesta = campus.llamar("leer", {"url": url})
        nombre = url
        if respuesta.startswith("Descargado:"):
            ruta = Path(respuesta.removeprefix("Descargado:").strip())
            nombre = ruta.name
            completo = extraer_texto(ruta, desde + MAX_CHARS_CAMPUS + 1)
        else:
            completo = respuesta
        hasta = desde + MAX_CHARS_CAMPUS
        texto = completo[desde:hasta]
        if desde and not texto.strip():
            return _responder(runtime, f"«{nombre}» no tiene más texto después de desde={desde}.")
        if not texto.strip():
            return _responder(runtime, f"«{nombre}» no tiene texto extraíble (¿escaneado?).")
        material = [_material(runtime, "campus", f"Campus: {nombre}", texto)]
        sigue = (f"\n[…el archivo continúa: llamá de nuevo con accion='leer', la misma url "
                 f"y desde={hasta} para leer el resto]") if len(completo) > hasta else ""
        return _responder(runtime, f"Campus: {nombre}\n{texto}{sigue}",
                          artifact={"accion": accion, "archivo": nombre}, material=material)
    except CampusNoConfigurado as e:
        return _responder(runtime, f"{e} Seguí sin el campus.")
    except Exception as e:   # noqa: BLE001
        return _responder(runtime, f"No se pudo acceder al campus ({e}). Seguí sin el campus.")


# --- 3. Búsqueda en internet (DuckDuckGo, sin API key) ------------------------------

@tool
def buscar_en_internet(consulta: str, runtime: ToolRuntime[Contexto]) -> Command:
    """TERCER PASO, OPCIONAL. Busca en internet con DuckDuckGo.

    Usala solo si, después del libro y el campus, falta un concepto muy actual,
    una herramienta técnica o un ejemplo práctico. Devuelve hasta 4 resultados
    con título, URL y resumen.

    Args:
        consulta: búsqueda concreta, en castellano o inglés.
    """
    from ddgs import DDGS

    resultados, error = [], None
    # Los backends gratuitos de ddgs fallan de forma intermitente ("No results found"): 3 intentos.
    for region in ("ar-es", "es-es", "wt-wt"):
        try:
            resultados = DDGS().text(consulta, region=region, max_results=4) or []
            break
        except Exception as e:   # noqa: BLE001
            error = e
    if not resultados:
        return _responder(runtime, f"La búsqueda web falló ({error}). Seguí con el material que ya tenés.")
    bloques, material, fuentes = [], [], []
    for i, r in enumerate(resultados, 1):
        resumen = (r.get("body") or "")[:MAX_CHARS_WEB]
        bloques.append(f"[{i}] {r.get('title', '')}\nURL: {r.get('href', '')}\n{resumen}")
        material.append(_material(runtime, "web", f"Web: {r.get('href', '')}", resumen))
        fuentes.append({"titulo": r.get("title", ""), "url": r.get("href", "")})
    return _responder(runtime, "\n\n".join(bloques) or "Sin resultados.",
                      artifact=fuentes, material=material)


# --- 4. Skill de generación para estud.ia ----------------------------------------------

SKILLS = cargar_skills()


def _material_del_turno(material: list[dict], turno: int) -> list[dict]:
    """Material del turno actual; si no hay (p. ej. "agregá 5 más"), el del último turno con material.

    Se ordena adjuntos -> libro -> campus -> web.
    """
    if not material:
        return []
    turnos = [m["turno"] for m in material]
    elegido = turno if turno in turnos else max(turnos)
    seleccion = [m for m in material if m["turno"] == elegido]
    prioridad = {"adjunto": -1, "libro": 0, "campus": 1, "web": 2}   # lo que subió el alumno, primero
    seleccion.sort(key=lambda m: prioridad.get(m["origen"], 3))
    return seleccion


def _texto_adjuntos(material: list[dict]) -> str:
    """Texto de los archivos subidos por el alumno, recortado al presupuesto.

    Es el único material que el modelo no vio como resultado de una tool, así que
    viaja junto con las instrucciones de la skill. Libro, campus y web ya están en
    la conversación: repetirlos solo gastaría tokens.
    """
    partes, total = [], 0
    for m in material:
        if m["origen"] != "adjunto":
            continue
        bloque = f"### {m['fuente']}\n{m['texto']}"[:MAX_CHARS_MATERIAL - total]
        if len(bloque) < 200:
            break
        partes.append(bloque)
        total += len(bloque)
    return "\n\n".join(partes)


def _mensajes_del_turno(mensajes: list) -> list:
    """Mensajes desde el último pedido del alumno (los recordatorios del sistema no cuentan)."""
    turno = []
    for m in reversed(mensajes):
        if m.type == "human" and not m.additional_kwargs.get("recordatorio", False):
            break
        turno.append(m)
    return turno


def _llamadas(mensajes: list, nombre: str) -> int:
    return sum(c["name"] == nombre for m in mensajes if m.type == "ai" for c in m.tool_calls)


@tool
def cargar_skill(nombre: str, runtime: ToolRuntime[Contexto]) -> Command:
    """Carga las instrucciones completas de una skill (ver la lista en el system prompt).

    Llamala antes de hacer la tarea de la skill y seguí sus instrucciones al pie de
    la letra. Para cerrar un pedido usá la skill "generar-banco-estudia".

    Args:
        nombre: nombre de la skill, p. ej. "generar-banco-estudia".
    """
    skill = SKILLS.get(nombre.strip())
    if skill is None:
        disponibles = ", ".join(SKILLS) or "ninguna"
        return _responder(runtime, f"No existe la skill «{nombre}». Skills disponibles: {disponibles}.")
    texto = skill.instrucciones
    fuentes: list[str] = []
    if skill.nombre == SKILL_BANCO:
        seleccion = _material_del_turno(runtime.state.get("material", []), runtime.context.turno)
        fuentes = sorted({m["fuente"] for m in seleccion})
        if fuentes:
            texto += "\n\n## Fuentes recopiladas (para citar)\n" + "\n".join(f"- {f}" for f in fuentes)
        if adjuntos := _texto_adjuntos(seleccion):
            texto += "\n\n## Material adjunto por el alumno (tiene prioridad)\n" + adjuntos
    return _responder(runtime, texto, artifact={"skill": skill.nombre, "fuentes": fuentes})


def _guardar(banco: dict) -> Path:
    nombre = re.sub(r"[^a-z0-9]+", "-", banco["titulo"].lower()).strip("-")[:40] or "banco"
    ruta = OUTPUT_DIR / f"{nombre}-{datetime.now():%Y%m%d-%H%M%S}.json"
    ruta.write_text(json.dumps(banco, ensure_ascii=False, indent=2), encoding="utf-8")
    return ruta


@tool
def guardar_banco_estudia(banco_json: str, runtime: ToolRuntime[Contexto],
                          cantidad: int = 10, tipos: list[TipoPregunta] | None = None) -> Command:
    """ÚLTIMO PASO OBLIGATORIO de la skill "generar-banco-estudia". Valida y guarda el banco.

    Recibe el JSON que escribiste siguiendo la skill. Si es válido, lo guarda y ES la
    respuesta final para el alumno. Si no, devuelve los errores: corregilos y volvé a
    llamarla con el JSON completo (hasta 3 intentos por pedido).

    Args:
        banco_json: el banco completo en JSON, como texto.
        cantidad: número de preguntas pedido (1 a 30). 10 si el alumno no dijo cuántas.
        tipos: tipos de pregunta pedidos por el alumno; vacío = mezclar todos.
    """
    cantidad = max(1, min(int(cantidad or 10), 30))
    mensajes = runtime.state.get("messages", [])
    if not _llamadas(mensajes, buscar_bibliografia.name):   # libro -> campus -> web, en código
        return _responder(
            runtime,
            "Todavía no consultaste la bibliografía oficial. Primero usá buscar_bibliografia "
            "con la materia y cada tema; después, explorar_campus; y recién entonces generá.",
        )
    # El AIMessage con esta llamada ya está en el estado: cuenta como intento.
    intento = _llamadas(_mensajes_del_turno(mensajes), guardar_banco_estudia.name)
    try:
        banco = extraer_json(banco_json)
        errores = validar_banco(banco, cantidad, list(tipos or []))
    except Exception as e:   # noqa: BLE001 - JSON inválido
        errores = [f"No se pudo leer el JSON: {e}"]
    if errores:
        if intento >= INTENTOS_GENERACION:
            return _responder(
                runtime,
                f"No se logró un banco válido en {INTENTOS_GENERACION} intentos. Errores: "
                + "; ".join(errores[:8]) + ". Avisale al alumno y sugerile pedir menos preguntas.",
            )
        return _responder(
            runtime,
            f"Intento {intento} de {INTENTOS_GENERACION}: el banco tiene estos errores. Corregilos y "
            "volvé a llamar a guardar_banco_estudia con el JSON completo corregido:\n- "
            + "\n- ".join(errores[:15]),
        )

    banco["titulo"] = banco.get("titulo") or "Banco de preguntas"
    ruta = _guardar(banco)
    contenido = json.dumps(banco, ensure_ascii=False, indent=2)
    usadas = _material_del_turno(runtime.state.get("material", []), runtime.context.turno)
    return _responder(
        runtime,
        contenido,
        artifact={
            "ok": True,
            "json": contenido,
            "archivo": str(ruta),
            "intentos": intento,
            "preguntas": len(banco["preguntas"]),
            "fuentes": sorted({m["fuente"] for m in usadas}),
        },
    )


TOOLS: list = [plan, buscar_bibliografia, explorar_campus, buscar_en_internet,
               cargar_skill, guardar_banco_estudia]


def banco_generado(mensaje) -> str | None:
    """JSON validado si `mensaje` es un resultado exitoso de guardar_banco_estudia."""
    datos = getattr(mensaje, "artifact", None)
    if getattr(mensaje, "type", "") == "tool" and isinstance(datos, dict) and datos.get("ok"):
        return datos["json"]
    return None
