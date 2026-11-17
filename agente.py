"""Agente generador de práctica para estud.ia: prompt, create_agent, streaming y conversaciones.

`stream_turn` ejecuta un turno y emite *eventos* (razonamiento, llamadas a
tools, resultados, texto) a medida que el agente avanza. `saved_turns`
reconstruye los mismos eventos desde la conversación guardada. Cada interfaz
(consola o Streamlit) solo los presenta.

Consola:    python agente.py
Streamlit:  streamlit run app.py
"""
import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from uuid import uuid4

from langchain.agents import create_agent
from langchain.agents.middleware import after_model, before_model, wrap_model_call
from langchain.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver

from llm import obtener_adaptador
from skills import catalogo
from tools import SKILLS, TOOLS, Contexto, EstadoEstudio, banco_generado, materias_con_bibliografia

HOY = date.today()

SYSTEM_PROMPT = f"""Sos el generador de práctica de la UTN FRM. Tu ÚNICA función es armar bancos de preguntas interactivos en JSON para la plataforma estud.ia, a partir del material oficial de la cátedra. La fecha de hoy es {HOY.isoformat()}.

El alumno te dice la MATERIA y los TEMAS (y opcionalmente la cantidad y los tipos de pregunta). Seguí SIEMPRE este orden estricto:

0. SIEMPRE empezá llamando a `plan` con los pasos como lista TO DO, aunque el pedido sea simple, y mantenela actualizada a medida que avanzás.

1. PRIMERO, bibliografía oficial: llamá a `buscar_bibliografia` con la materia y una consulta concreta por cada tema pedido. Es la fuente principal: las definiciones y conceptos tienen que salir de ahí.
2. SEGUNDO, campus UTN FRM: usá `explorar_campus` para ver el enfoque de los docentes. Empezá con accion="materias", buscá la materia, pedí accion="contenido" y leé con accion="leer" los 1 o 2 archivos (filminas, apuntes, guías) más relacionados con los temas. Si el campus no está disponible, seguí.
3. TERCERO, OPCIONAL: usá `buscar_en_internet` solo si después del libro y del campus falta un concepto muy actual, una herramienta técnica o un ejemplo práctico. No la uses si el material alcanza.
4. CUARTO, cierre OBLIGATORIO con la skill `generar-banco-estudia`: llamá a `cargar_skill` con ese nombre, seguí sus instrucciones para escribir vos mismo el banco en JSON con el material recopilado y entregalo con `guardar_banco_estudia` (cantidad: 10 si el alumno no dijo; tipos: vacío si no pidió ninguno). Si devuelve errores, corregilos y volvé a llamarla. Cuando el banco es válido, ese es el resultado final.

Skills disponibles (cargalas con `cargar_skill` cuando las necesites; no hace falta volver a cargar una que ya cargaste en esta conversación):
{catalogo(SKILLS)}

Reglas:
- No respondas preguntas de teoría ni charles: todo pedido termina en un banco de preguntas. Si falta la materia o los temas, pedilos en una sola oración.
- Si el mensaje trae «📎 Adjuntos», el texto de esos archivos se te entrega al cargar la skill y tiene prioridad: igual buscá en la bibliografía, pero no hace falta buscarlos en el campus.
- Si una tool devuelve un error, leelo y seguí con el siguiente paso; no repitas la misma llamada más de 2 veces.
- Si el alumno pide cambios sobre un banco anterior ("agregá 5 de verdadero/falso", "hacelo más difícil"), usá la conversación previa: no hace falta volver a buscar si el material ya está, aplicá la skill directamente con los nuevos parámetros, salvo que pida temas nuevos.
- Materias con bibliografía cargada: {", ".join(materias_con_bibliografia()) or "ninguna todavía"}."""

DIR = Path(__file__).parent
CONV_FILE = DIR / "conversaciones.txt"   # un thread_id por línea; el último es el activo


@before_model(can_jump_to=["end"])
def cerrar_con_json(state: EstadoEstudio, runtime) -> dict | None:
    """Middleware propio: si guardar_banco_estudia validó el banco, termina el turno.

    La respuesta final pasa a ser el JSON validado, tal cual: el modelo no lo
    vuelve a escribir (no puede deformarlo) y se ahorra una llamada al LLM, que
    en un tier gratuito con límite de peticiones importa.
    """
    for mensaje in reversed(state["messages"]):
        if mensaje.type != "tool":
            break
        if contenido := banco_generado(mensaje):
            return {"messages": [AIMessage(contenido)], "jump_to": "end"}
    return None


def _turno_actual(mensajes: list) -> list:
    """Mensajes desde el último pedido del alumno (sin contar los recordatorios)."""
    turno = []
    for m in reversed(mensajes):
        if m.type == "human" and not _es_recordatorio(m):
            break
        turno.append(m)
    return turno


@wrap_model_call
def planificar_primero(request, handler):
    """Middleware propio: la primera llamada al modelo de cada turno solo puede usar `plan`.

    El prompt ya lo pide, pero algunos modelos lo saltean; forzar tool_choice lo garantiza.
    """
    if not any(m.type == "ai" for m in _turno_actual(request.messages)):
        request = request.override(tool_choice="plan")
    return handler(request)


RECORDATORIO = (
    "[recordatorio del sistema] Ya consultaste las fuentes de este pedido. Ahora cargá la skill "
    "generar-banco-estudia con cargar_skill (si no la cargaste), escribí el banco y entregalo "
    "con guardar_banco_estudia."
)


def _es_recordatorio(m) -> bool:
    return m.type == "human" and m.additional_kwargs.get("recordatorio", False)


@after_model(can_jump_to=["model"])
def exigir_cierre(state: EstadoEstudio, runtime) -> dict | None:
    """Middleware propio: red de seguridad para el paso 4 (cierre obligatorio).

    Si el modelo termina el turno sin entregar el banco con guardar_banco_estudia pero ya consultó
    la bibliografía (p. ej. un modelo gratuito que se "pierde" en su
    razonamiento), se le recuerda UNA vez que genere el banco y vuelve al modelo.
    """
    mensajes = state["messages"]
    ultimo = mensajes[-1]
    if ultimo.type != "ai" or ultimo.tool_calls:
        return None
    turno = _turno_actual(mensajes)
    if any(_es_recordatorio(m) for m in turno):
        return None   # ya se le recordó en este turno: no insistir (evita bucles)
    consulto = any(c["name"] == "buscar_bibliografia" for m in turno if m.type == "ai"
                   for c in m.tool_calls)
    if not consulto:
        return None   # p. ej. le pidió al alumno la materia o los temas
    return {
        "messages": [HumanMessage(RECORDATORIO, additional_kwargs={"recordatorio": True})],
        "jump_to": "model",
    }


adaptador = obtener_adaptador()   # patrón Adapter: el agente no sabe qué proveedor hay detrás

agent = create_agent(
    model=adaptador.modelo_chat(),
    tools=TOOLS,
    system_prompt=SYSTEM_PROMPT,
    middleware=[planificar_primero, cerrar_con_json, exigir_cierre],
    state_schema=EstadoEstudio,     # messages + material recopilado
    context_schema=Contexto,        # número de turno, de solo lectura
    checkpointer=SqliteSaver(sqlite3.connect(DIR / "memoria.db", check_same_thread=False)),
)


# --- Conversaciones -------------------------------------------------------------

def _config(thread_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 100}   # ~4 pasos del grafo por vuelta de tools


def _thread_ids() -> list[str]:
    return CONV_FILE.read_text().split() if CONV_FILE.exists() else []


def select_thread(thread_id: str) -> RunnableConfig:
    """Marca una conversación como la activa: pasa a ser la última del archivo."""
    ids = [i for i in _thread_ids() if i != thread_id]
    CONV_FILE.write_text("\n".join(ids + [thread_id]))
    return _config(thread_id)


def new_thread() -> RunnableConfig:
    return select_thread(str(uuid4()))


def resume_thread() -> RunnableConfig:
    """Retoma la última conversación usada; si no hay ninguna, crea una nueva."""
    ids = _thread_ids()
    return select_thread(ids[-1]) if ids else new_thread()


def saved_state(config: RunnableConfig) -> dict:
    return agent.get_state(config).values


def cancelar_turno(config: RunnableConfig) -> None:
    """Cierra un turno detenido a mitad: responde las tools que quedaron sin resultado.

    Sin esto el historial queda con tool calls colgadas y el proveedor rechaza el turno siguiente.
    """
    mensajes = saved_state(config).get("messages", [])
    respondidas = {m.tool_call_id for m in mensajes if m.type == "tool"}
    pendientes = [c["id"] for m in mensajes if m.type == "ai" for c in m.tool_calls if c["id"] not in respondidas]
    if pendientes:
        agent.update_state(config, {"messages": [
            ToolMessage("Cancelado: el alumno detuvo el turno.", tool_call_id=i) for i in pendientes]},
            as_node="tools")


def list_threads(limit: int = 6) -> list[tuple[str, str]]:
    """Últimas conversaciones con mensajes, la más reciente primero: (thread_id, título)."""
    conversaciones = []
    for thread_id in reversed(_thread_ids()):
        mensajes = saved_state(_config(thread_id)).get("messages", [])
        if mensajes:
            conversaciones.append((thread_id, mensajes[0].text[:40]))
        if len(conversaciones) == limit:
            break
    return conversaciones


# --- Eventos: lo único que las interfaces necesitan conocer ----------------------------

MAX_CHARS_RAZONAMIENTO = 3000


def _razonamiento(m) -> str:
    """Razonamiento del modelo, si el proveedor lo expone (Gemini sí; Llama 3.3 no)."""
    bloques = [
        b.get("reasoning", "") for b in m.content_blocks
        if b.get("type") == "reasoning" and b.get("reasoning")
    ]
    texto = ("\n\n".join(bloques)
             or m.additional_kwargs.get("reasoning_content", "")
             or m.additional_kwargs.get("reasoning", ""))
    if len(texto) > MAX_CHARS_RAZONAMIENTO:   # un bucle del modelo no debe saturar la UI
        texto = texto[:MAX_CHARS_RAZONAMIENTO] + " […recortado]"
    return texto


def eventos(mensajes: list) -> Iterator[dict]:
    """Traduce mensajes del grafo a eventos para mostrar."""
    for m in mensajes:
        if _es_recordatorio(m):
            yield {"tipo": "recordatorio", "texto": m.text}
        elif m.type == "ai":
            if razonamiento := _razonamiento(m):
                yield {"tipo": "razonamiento", "texto": razonamiento}
            if m.text:
                yield {"tipo": "texto", "texto": m.text}
            for call in m.tool_calls:
                yield {"tipo": "llamada", "id": call["id"], "nombre": call["name"], "args": call["args"]}
        elif m.type == "tool":
            yield {"tipo": "resultado", "id": m.tool_call_id, "texto": m.text, "datos": m.artifact}


def stream_turn(user_input: str, config: RunnableConfig, turno: int,
                adjuntos: list[dict] | None = None) -> Iterator[dict]:
    """Ejecuta un turno del agente y emite sus eventos a medida que ocurren.

    `adjuntos`: material de los archivos que subió el alumno (se entrega al cargar la skill).
    """
    vistos = len(saved_state(config).get("messages", []))
    # stream_mode="values": tras cada paso llega el estado completo; se emiten solo los mensajes nuevos.
    for estado in agent.stream(
        {"messages": [{"role": "user", "content": user_input}], "material": adjuntos or []},
        config,
        context=Contexto(turno=turno),
        stream_mode="values",
    ):
        yield from eventos(estado["messages"][vistos:])
        vistos = len(estado["messages"])


def saved_turns(config: RunnableConfig) -> list[tuple[str, list[dict]]]:
    """La conversación guardada como turnos: (pedido, eventos del agente)."""
    turnos: list[tuple[str, list[dict]]] = []
    for m in saved_state(config).get("messages", []):
        if m.type == "human" and not _es_recordatorio(m):
            turnos.append((m.text, []))
        elif turnos:
            turnos[-1][1].extend(eventos([m]))
    return turnos


# --- Consola ------------------------------------------------------------------

def print_evento(e: dict) -> None:
    tipo = e["tipo"]
    if tipo == "razonamiento":
        print(f"[razonamiento] {e['texto']}\n")
    elif tipo == "llamada":
        print(f"[tool] {e['nombre']}({e['args']})")
    elif tipo == "resultado":
        print(f"[resultado] {e['texto'][:300]}\n")
    elif tipo == "recordatorio":
        print("[middleware] se le recordó al modelo que genere el banco\n")
    elif tipo == "texto":
        print(f"Agente:\n{e['texto']}\n")


def main() -> None:
    print(f"Generador estud.ia ({adaptador}). Escribí 'salir' para terminar.\n")
    config = resume_thread()
    turnos = saved_turns(config)
    for pregunta, evs in turnos:
        print(f"Vos: {pregunta}")
        for e in evs:
            print_evento(e)
    turno = len(turnos)

    while True:
        user_input = input("Vos: ").strip()
        if user_input.lower() in {"salir", "exit", "quit"}:
            break
        if not user_input:
            continue
        turno += 1
        for e in stream_turn(user_input, config, turno):
            print_evento(e)


if __name__ == "__main__":
    main()
