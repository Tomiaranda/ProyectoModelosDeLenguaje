"""UI de Streamlit: conversa con el agente y muestra en vivo cada paso.

La lógica vive en agente.py; acá solo se dibujan los eventos que emite.

Ejecutar con:  streamlit run app.py
"""
import json
import os
import sys

import streamlit as st

st.set_page_config(page_title="Generador estud.ia · UTN FRM", page_icon="🎓", layout="wide")

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


def render_eventos(eventos) -> list[dict]:
    """Dibuja los eventos a medida que llegan (en vivo o desde el historial)."""
    emitidos = []
    llamadas = {}   # id del tool call -> (evento de la llamada, contenedor st.status)
    for e in eventos:
        emitidos.append(e)
        tipo = e["tipo"]
        if tipo == "razonamiento":
            with st.expander("💭 Razonamiento del modelo", expanded=False):
                st.markdown("  \n".join(l.strip() for l in e["texto"].splitlines()))
        elif tipo == "recordatorio":
            st.info("🔁 El modelo terminó sin generar el banco: el middleware le recordó el paso 4.")
        elif tipo == "texto":
            if not render_banco(e["texto"], f"{id(e)}-{len(emitidos)}"):
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
