"""Las 4 tools del agente y su estado.

Orden de uso que impone el system prompt (ver agente.py):
    1. buscar_bibliografia   RAG sobre los libros oficiales de la cátedra (SQLite)
    2. explorar_campus       Campus Virtual UTN FRM vía MCP
    3. buscar_en_internet    DuckDuckGo, opcional, sin API key
    4. generar_banco_estudia arma y valida el JSON para estud.ia (cierre)

Las tres primeras guardan lo que encuentran en el estado del agente
(`material`). La generadora lee ese material del estado: el modelo no tiene que
volver a escribirlo como argumento (ahorra tokens y evita que lo deforme), y
puede comprobar que la bibliografía oficial se consultó antes de generar.

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

from campus.archivos import extraer_texto
from campus.cliente_mcp import CampusNoConfigurado, campus
from estudia.formato import SISTEMA_GENERADOR, armar_prompt
from estudia.validador import extraer_json, validar_banco
from llm import obtener_adaptador, obtener_embeddings
from rag import store

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# Presupuesto de contexto. Llama 3.3 70B tiene 128k tokens, pero cada token
# cuenta contra los límites del tier gratuito: lo recuperado se recorta.
MAX_CHARS_FRAGMENTO_LIBRO = 2200   # ~550 tokens por fragmento de 2 páginas
MAX_CHARS_CAMPUS = 5000            # contenido de una materia o un archivo
MAX_CHARS_WEB = 600                # por resultado
MAX_CHARS_MATERIAL = 16000         # total que recibe la generadora (~4k tokens)
INTENTOS_GENERACION = 3

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
                    course_id: int | None = None, url: str | None = None) -> Command:
    """SEGUNDO PASO. Explora el Campus Virtual UTN FRM (Moodle) mediante su servidor MCP.

    Sirve para cruzar la teoría del libro con el enfoque, los ejercicios y el
    recorte de temas de los docentes. Flujo: "materias" -> course_id ->
    "contenido" -> url de un apunte, filmina o guía -> "leer".

    Args:
        accion: "materias" lista las materias del alumno con su course_id;
            "contenido" muestra unidades y archivos de una materia (requiere course_id);
            "leer" abre una URL del campus y devuelve el texto de la página o del
            archivo (PDF, PPTX, DOCX) (requiere url).
        course_id: id de la materia, sale de accion="materias".
        url: URL del campus, sale de accion="contenido".
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
            texto = extraer_texto(ruta, MAX_CHARS_CAMPUS)
        else:
            texto = respuesta[:MAX_CHARS_CAMPUS]
        if not texto.strip():
            return _responder(runtime, f"«{nombre}» no tiene texto extraíble (¿escaneado?).")
        material = [_material(runtime, "campus", f"Campus: {nombre}", texto)]
        return _responder(runtime, f"Campus: {nombre}\n{texto}",
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


# --- 4. Generadora de práctica para estud.ia -------------------------------------------

def _material_para_generar(material: list[dict], turno: int) -> tuple[str, list[dict]]:
    """Material del turno actual; si no hay (p. ej. "agregá 5 más"), el del último turno con material.

    Se ordena libro -> campus -> web y se recorta al presupuesto total.
    """
    if not material:
        return "", []
    turnos = [m["turno"] for m in material]
    elegido = turno if turno in turnos else max(turnos)
    seleccion = [m for m in material if m["turno"] == elegido]
    prioridad = {"libro": 0, "campus": 1, "web": 2}
    seleccion.sort(key=lambda m: prioridad.get(m["origen"], 3))
    partes, total, usadas = [], 0, []
    for m in seleccion:
        bloque = f"### {m['fuente']}\n{m['texto']}"
        if total + len(bloque) > MAX_CHARS_MATERIAL:
            bloque = bloque[:MAX_CHARS_MATERIAL - total]
        if len(bloque) < 200:
            break
        partes.append(bloque)
        usadas.append(m)
        total += len(bloque)
    return "\n\n".join(partes), usadas


def _guardar(banco: dict) -> Path:
    nombre = re.sub(r"[^a-z0-9]+", "-", banco["titulo"].lower()).strip("-")[:40] or "banco"
    ruta = OUTPUT_DIR / f"{nombre}-{datetime.now():%Y%m%d-%H%M%S}.json"
    ruta.write_text(json.dumps(banco, ensure_ascii=False, indent=2), encoding="utf-8")
    return ruta


@tool
def generar_banco_estudia(titulo: str, temas: str, runtime: ToolRuntime[Contexto],
                          cantidad: int = 10, tipos: list[TipoPregunta] | None = None,
                          descripcion: str = "", indicaciones: str = "") -> Command:
    """ÚLTIMO PASO OBLIGATORIO. Genera y valida el banco de preguntas JSON para estud.ia.

    Usa automáticamente todo el material recopilado en este turno con las otras
    tools (libro, campus, web): NO hace falta pasarle el material. Si la
    validación falla, la herramienta reintenta sola. Su resultado ES la
    respuesta final para el alumno.

    Args:
        titulo: nombre corto del test, p. ej. "Modelos de Lenguaje - Transformers".
        temas: temas que deben cubrir las preguntas, separados por coma.
        cantidad: número de preguntas (1 a 30). 10 si el alumno no dijo cuántas.
        tipos: tipos de pregunta pedidos por el alumno; vacío = mezclar todos.
        descripcion: descripción breve del test.
        indicaciones: pedidos extra del alumno (dificultad, enfoque, etc.).
    """
    cantidad = max(1, min(int(cantidad or 10), 30))
    material = runtime.state.get("material", [])
    consulto_libros = any(
        call["name"] == buscar_bibliografia.name
        for m in runtime.state.get("messages", []) if m.type == "ai"
        for call in m.tool_calls
    )
    if not consulto_libros:   # la jerarquía libro -> campus -> web se hace cumplir en código
        return _responder(
            runtime,
            "Todavía no consultaste la bibliografía oficial. Primero usá buscar_bibliografia "
            "con la materia y cada tema; después, explorar_campus; y recién entonces generá.",
        )
    texto_material, usadas = _material_para_generar(material, runtime.context.turno)
    if not texto_material:
        return _responder(runtime, "No hay material recopilado: consultá primero las fuentes.")

    prompt = armar_prompt(cantidad, list(tipos or []), texto_material, titulo, temas, indicaciones)
    try:
        adaptador = obtener_adaptador()
    except Exception as e:   # noqa: BLE001
        return _responder(runtime, f"No se pudo crear el modelo generador: {e}")

    errores: list[str] = []
    respuesta = ""
    for intento in range(1, INTENTOS_GENERACION + 1):
        pedido = prompt
        if errores:
            pedido += (
                "\n\nTu respuesta anterior tenía estos errores; devolvé el JSON completo corregido:\n- "
                + "\n- ".join(errores[:15]) + f"\n\nRespuesta anterior:\n{respuesta[:6000]}"
            )
        try:
            respuesta = adaptador.completar(SISTEMA_GENERADOR, pedido)
            banco = extraer_json(respuesta)
            errores = validar_banco(banco, cantidad, list(tipos or []))
        except Exception as e:   # noqa: BLE001 - JSON inválido o error del proveedor
            errores = [f"No se pudo leer el JSON: {e}"]
            continue
        if not errores:
            break
    if errores:
        return _responder(
            runtime,
            f"No se logró un banco válido en {INTENTOS_GENERACION} intentos. Errores: "
            + "; ".join(errores[:8]) + ". Podés reintentar con menos preguntas.",
        )

    banco["titulo"] = banco.get("titulo") or titulo
    if descripcion and not banco.get("descripcion"):
        banco["descripcion"] = descripcion
    ruta = _guardar(banco)
    contenido = json.dumps(banco, ensure_ascii=False, indent=2)
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


TOOLS: list = [buscar_bibliografia, explorar_campus, buscar_en_internet, generar_banco_estudia]


def banco_generado(mensaje) -> str | None:
    """JSON validado si `mensaje` es un resultado exitoso de generar_banco_estudia."""
    datos = getattr(mensaje, "artifact", None)
    if getattr(mensaje, "type", "") == "tool" and isinstance(datos, dict) and datos.get("ok"):
        return datos["json"]
    return None
