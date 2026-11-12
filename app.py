"""UI de Streamlit: conversa con el agente y muestra en vivo cada paso.

La lógica vive en agente.py; acá solo se dibujan los eventos que emite.

Ejecutar con:  streamlit run app.py
"""
import json
import os
import sys

import streamlit as st

st.set_page_config(page_title="Generador estud.ia · UTN FRM", page_icon="🎓", layout="wide")

# Chat estilo shadcn: burbuja oscura a la derecha para el usuario, asistente sin
# burbuja y un input tipo "input-group". Los colores vienen de .streamlit/config.toml.
st.markdown("""<style>
.block-container { max-width: 52rem; }
[data-testid="stChatMessage"] { background: transparent; padding: .25rem 0; gap: .75rem; }
[data-testid="stChatMessageAvatarUser"] { display: none; }
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) { flex-direction: row-reverse; }
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) [data-testid="stChatMessageContent"] {
  flex: 0 1 auto; max-width: 80%; background: #18181b; color: #fafafa;
  padding: .6rem 1rem; border-radius: 1.25rem 1.25rem .35rem 1.25rem;
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) [data-testid="stChatMessageContent"] * { color: inherit; }
[data-testid="stChatMessageAvatarAssistant"] {
  background: #f4f4f5; border: 1px solid #e4e4e7; border-radius: 9999px;
}
[data-testid="stChatInput"] {
  border: 1px solid #e4e4e7; border-radius: 1rem; background: #fff;
  box-shadow: 0 1px 2px rgb(0 0 0 / .05), 0 4px 12px rgb(0 0 0 / .04);
}
[data-testid="stChatInput"]:focus-within { border-color: #a1a1aa; box-shadow: 0 0 0 3px rgb(161 161 170 / .25); }
[data-testid="stChatInputSubmitButton"] { background: #18181b; color: #fff; border-radius: .6rem; }
[data-testid="stExpander"] details, [data-testid="stStatusWidget"] { border-radius: .75rem; }
.trace { border: 1px solid #e4e4e7; border-radius: .75rem; padding: .75rem 1rem; margin: .25rem 0 .75rem; }
.trace-head { display: flex; justify-content: space-between; font-size: .8rem; color: #71717a; margin-bottom: .5rem; }
.trace-head b { color: #09090b; font-weight: 600; }
.trace-bar { height: 3px; background: #f4f4f5; border-radius: 9999px; margin-bottom: .6rem; overflow: hidden; }
.trace-bar span { display: block; height: 100%; background: #18181b; transition: width .3s; }
.trace ol { list-style: none; margin: 0; padding: 0; }
.trace li { position: relative; padding: 0 0 .55rem 1.6rem; font-size: .9rem; margin: 0; }
.trace li:not(:last-child)::after { content: ""; position: absolute; left: .45rem; top: 1.1rem; bottom: .1rem; width: 1px; background: #e4e4e7; }
.trace li::before { content: ""; position: absolute; left: 0; top: .2rem; width: .9rem; height: .9rem;
  border-radius: 9999px; border: 1.5px solid #a1a1aa; box-sizing: border-box; }
.trace li.hecho { color: #a1a1aa; text-decoration: line-through; }
.trace li.hecho::before { background: #18181b; border-color: #18181b;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath d='M4 8.5l2.5 2.5L12 5.5' fill='none' stroke='white' stroke-width='2'/%3E%3C/svg%3E"); }
.trace li.en_curso { font-weight: 500; }
.trace li.en_curso::before { border-color: #18181b; border-top-color: transparent; animation: girar .8s linear infinite; }
@keyframes girar { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .trace li.en_curso::before { animation: none; border-top-color: #18181b; } }
</style>""", unsafe_allow_html=True)

CLAVES = {
    "CAMPUS_USER": "Usuario del campus",
    "CAMPUS_PASS": "Contraseña del campus",
    "OPENROUTER_API_KEY": "OpenRouter API key",
    "GOOGLE_API_KEY": "Google API key",
}


def render_claves() -> None:
    """Carga las claves en os.environ, así no hace falta ponerlas en el .env."""
    faltan = [v for v in CLAVES if not os.getenv(v) or os.getenv(v).startswith("tu_")]
    with st.sidebar.expander("🔑 Claves", expanded=bool(faltan)):
        with st.form("claves"):
            valores = {v: st.text_input(etiqueta, type="password",
                                        placeholder="faltante" if v in faltan else "cargada (vacío = no cambiar)")
                       for v, etiqueta in CLAVES.items()}
            if st.form_submit_button("Guardar"):
                for v, valor in valores.items():
                    if valor.strip():
                        os.environ[v] = valor.strip()
                # ponytail: los módulos leen las claves al importarse; se reimportan para tomarlas.
                # La conexión MCP previa al campus queda viva hasta reiniciar la app.
                for m in list(sys.modules):
                    if m.split(".")[0] in ("agente", "tools", "llm", "campus"):
                        del sys.modules[m]
                st.session_state.pop("config", None)
                st.rerun()


render_claves()

try:
    from agente import (
        adaptador, list_threads, new_thread, resume_thread, saved_turns, select_thread, stream_turn,
    )
    from tools import materias_con_bibliografia
except RuntimeError as e:   # p. ej. faltan claves en el .env
    st.error(str(e))
    st.stop()

ESTUDIA_URL = "https://estud-ia-blond.vercel.app/"
PASOS_EXPANDIDOS = False   # True: las tools se muestran desplegadas


def init_session(config) -> None:
    st.session_state.config = config
    st.session_state.historial = saved_turns(config)   # lista de (pedido, eventos)


def titulo_llamada(e: dict) -> str:
    nombre, args = e["nombre"], e["args"]
    if nombre == "buscar_bibliografia":
        return f"📚 1 · Bibliografía de {args.get('materia', '?')}: «{args.get('consulta', '')}»"
    if nombre == "explorar_campus":
        detalle = args.get("url") or args.get("course_id") or ""
        return f"🏫 2 · Campus UTN FRM: {args.get('accion', '')} {detalle}".strip()
    if nombre == "buscar_en_internet":
        return f"🌐 3 · Internet: «{args.get('consulta', '')}»"
    if nombre == "generar_banco_estudia":
        tipos = ", ".join(args.get("tipos") or []) or "tipos mezclados"
        return f"🧩 4 · Generar banco: {args.get('cantidad', 10)} preguntas ({tipos})"
    return f"🔧 {nombre}"


def detalle_resultado(llamada: dict, r: dict) -> str | None:
    """Resumen de lo que devolvió la tool. Devuelve "error" si la tool falló."""
    nombre, datos = llamada["nombre"], r["datos"]
    st.caption("Argumentos")
    st.json(llamada["args"], expanded=False)
    st.caption("Resultado")
    if nombre == "buscar_bibliografia":
        fragmentos = (datos or {}).get("fragmentos", [])
        if not fragmentos:
            st.warning(r["texto"])
            return "error"
        for f in fragmentos:
            st.markdown(f"- 📄 **{f['libro']}**, págs. {f['paginas']} "
                        f"(similitud {f['similitud']}): _{f['vista']}…_")
    elif nombre == "buscar_en_internet" and isinstance(datos, list) and datos:
        for f in datos:
            st.markdown(f"- [{f['titulo']}]({f['url']})")
    elif nombre == "generar_banco_estudia":
        if not (isinstance(datos, dict) and datos.get("ok")):
            st.warning(r["texto"])
            return "error"
        st.markdown(f"✅ {datos['preguntas']} preguntas válidas en {datos['intentos']} intento(s).")
        st.markdown("**Fuentes usadas:**\n" + "\n".join(f"- {f}" for f in datos["fuentes"]))
    else:
        texto = r["texto"]
        st.text(texto[:800] + ("…" if len(texto) > 800 else ""))
        if texto.startswith(("No se pudo", "El campus no", "Falta", "La búsqueda web falló")):
            return "error"
    return None


def render_banco(texto: str, clave: str) -> bool:
    """Si el texto es un banco de estud.ia, lo muestra con botón de descarga."""
    try:
        banco = json.loads(texto)
    except (json.JSONDecodeError, TypeError):
        return False
    if not (isinstance(banco, dict) and "preguntas" in banco):
        return False
    st.success(f"**{banco.get('titulo', 'Banco')}** · {len(banco['preguntas'])} preguntas listas "
               f"para cargar en [estud.ia]({ESTUDIA_URL}).")
    st.download_button("⬇️ Descargar JSON", texto.encode("utf-8"),
                       file_name=f"{banco.get('titulo', 'banco')}.json",
                       mime="application/json", key=f"dl-{clave}")
    st.code(texto, language="json")
    return True


def html_plan(pasos: list[dict], terminado: bool = False) -> str:
    """Lista TO DO estilo trace: ✓ hecho, girando en curso, ○ pendiente."""
    from html import escape

    estados = ["hecho" if terminado else p.get("estado", "pendiente") for p in pasos]
    hechos = estados.count("hecho")
    items = "".join(f'<li class="{e}">{escape(p.get("paso", ""))}</li>' for p, e in zip(pasos, estados))
    return (f'<div class="trace"><div class="trace-head"><b>Plan</b><span>{hechos}/{len(pasos)}</span></div>'
            f'<div class="trace-bar"><span style="width:{100 * hechos // max(len(pasos), 1)}%"></span></div>'
            f"<ol>{items}</ol></div>")


def render_eventos(eventos) -> list[dict]:
    """Dibuja los eventos a medida que llegan (en vivo o desde el historial)."""
    emitidos = []
    llamadas = {}   # id del tool call -> (evento de la llamada, contenedor st.status)
    plan, pasos, ids_plan = None, [], set()   # el plan se dibuja una vez y se actualiza en el lugar
    for e in eventos:
        emitidos.append(e)
        tipo = e["tipo"]
        if tipo == "llamada" and e["nombre"] == "plan":
            ids_plan.add(e["id"])
            pasos = e["args"].get("pasos") or pasos
            plan = plan or st.empty()
            plan.markdown(html_plan(pasos), unsafe_allow_html=True)
            continue
        if tipo == "resultado" and e["id"] in ids_plan:
            continue
        if tipo == "razonamiento":
            with st.expander("💭 Razonamiento del modelo", expanded=False):
                st.markdown("  \n".join(l.strip() for l in e["texto"].splitlines()))
        elif tipo == "recordatorio":
            st.info("🔁 El modelo terminó sin generar el banco: el middleware le recordó el paso 4.")
        elif tipo == "texto":
            if render_banco(e["texto"], f"{id(e)}-{len(emitidos)}"):
                if plan:   # el banco cierra el turno: el plan queda completo
                    plan.markdown(html_plan(pasos, terminado=True), unsafe_allow_html=True)
            else:
                st.markdown(e["texto"])
        elif tipo == "llamada":
            status = st.status(titulo_llamada(e), expanded=PASOS_EXPANDIDOS)   # "running" hasta el resultado
            llamadas[e["id"]] = (e, status)
        elif tipo == "resultado" and e["id"] in llamadas:
            llamada, status = llamadas[e["id"]]
            with status:
                estado = detalle_resultado(llamada, e)
            status.update(state=estado or "complete")
    return emitidos


def render_sidebar() -> None:
    actual = st.session_state.config["configurable"]["thread_id"]
    with st.sidebar:
        st.header("🎓 Generador estud.ia")
        st.caption(f"Modelo: `{adaptador}`")
        st.subheader("Bibliografía cargada")
        materias = materias_con_bibliografia()
        if materias:
            for materia, libros in materias.items():
                st.markdown(f"**{materia}**: " + ", ".join(libros))
        else:
            st.warning("No hay libros indexados. Ejecutá `python -m rag.ingest`.")

        st.subheader("Conversaciones")
        if st.button("➕ Nueva conversación", disabled=not st.session_state.historial):
            init_session(new_thread())
            st.rerun()
        for thread_id, titulo in list_threads():
            if st.button(titulo, key=thread_id, disabled=thread_id == actual,
                         use_container_width=True):
                init_session(select_thread(thread_id))
                st.rerun()
        st.divider()
        st.markdown(f"[Abrir estud.ia ↗]({ESTUDIA_URL})")


if "config" not in st.session_state:
    init_session(resume_thread())

st.title("🎓 Generador de práctica para estud.ia")
st.caption("Pedí preguntas sobre una materia y sus temas. El agente consulta los libros de la "
           "cátedra, el campus de la UTN FRM y, si hace falta, internet; y devuelve un JSON "
           "listo para cargar en estud.ia.")
render_sidebar()

for pregunta, eventos in st.session_state.historial:
    st.chat_message("user").markdown(pregunta)
    with st.chat_message("assistant"):
        render_eventos(eventos)

if prompt := st.chat_input("Ej.: 10 preguntas de Modelos de Lenguaje sobre embeddings y transformers"):
    turno = len(st.session_state.historial) + 1
    st.chat_message("user").markdown(prompt)
    with st.chat_message("assistant"):
        try:
            with st.spinner("El agente está trabajando..."):
                eventos = render_eventos(stream_turn(prompt, st.session_state.config, turno))
        except Exception as e:   # noqa: BLE001
            st.error(f"El agente falló: {e}")
            st.stop()
    st.session_state.historial.append((prompt, eventos))
    st.rerun()
