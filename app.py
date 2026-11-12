"""UI de Streamlit: conversa con el agente y muestra en vivo cada paso.

La lógica vive en agente.py; acá solo se dibujan los eventos que emite.

Ejecutar con:  streamlit run app.py
"""
import json
import os
import sys

import streamlit as st
from dotenv import load_dotenv

load_dotenv()   # antes de render_claves: así las claves recordadas no figuran como faltantes

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
[data-testid="stChatInputSubmitButton"] { background: #18181b; color: #fff; border-radius: 9999px; }
[data-testid="stChatInputSubmitButton"]:disabled { background: #e4e4e7; color: #a1a1aa; }
[data-testid="stChatInputFileUploadButton"] { border: 1px solid #e4e4e7; border-radius: 9999px; }
.tr { margin: .15rem 0 .5rem; font-size: .875rem; color: #71717a; }
.tr summary { display: inline-flex; align-items: center; gap: .45rem; cursor: pointer; list-style: none;
  padding: .15rem .35rem; margin-left: -.35rem; border-radius: .5rem; }
.tr summary::-webkit-details-marker { display: none; }
.tr summary:hover { color: #09090b; background: #f4f4f5; }
.tr summary:focus-visible { outline: 2px solid #a1a1aa; outline-offset: 2px; }
.tr-ico { width: 1rem; height: 1rem; flex: none; }
.tr-chev { transition: transform .15s; }
.tr[open] .tr-chev { transform: rotate(90deg); }
.tr-body { margin: .35rem 0 0 .45rem; padding-left: .95rem; border-left: 1px solid #e4e4e7; }
.tr-pre { white-space: pre-wrap; max-height: 20rem; overflow: auto; }
.tr-line { display: flex; align-items: center; gap: .45rem; padding: .2rem 0; color: #71717a; text-decoration: none; }
a.tr-line:hover { color: #09090b; }
.tr-line > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tr-out { opacity: .6; }
.tr-running { color: #09090b; }
.tr-spin { width: .9rem; height: .9rem; flex: none; border-radius: 9999px; border: 2px solid #d4d4d8;
  border-top-color: #18181b; animation: girar .8s linear infinite; }
@media (prefers-reduced-motion: reduce) { .tr-spin { animation: none; } }
/* Colores del ícono: "Pensó" en celeste y cada tool con el suyo */
.tr-pensar { --c: #0369a1; --c-bg: #f0f9ff; }
.tr-violeta { --c: #6d28d9; --c-bg: #f5f3ff; }
.tr-verde { --c: #047857; --c-bg: #ecfdf5; }
.tr-azul { --c: #1d4ed8; --c-bg: #eff6ff; }
.tr-grafito { --c: #3f3f46; --c-bg: #f4f4f5; }
/* Solo el ícono lleva color; el texto queda gris como el resto del trace */
.tr[class*="tr-"]:not(.tr-error) summary > .tr-ico:not(.tr-chev) { color: var(--c, #71717a); }
.tr[class*="tr-"]:not(.tr-error) .tr-spin { border-color: var(--c-bg, #d4d4d8); border-top-color: var(--c, #18181b); }
.tr-error summary, .tr-error .tr-line { color: #dc2626; }
.tr-label { font-size: .7rem; text-transform: uppercase; letter-spacing: .04em; color: #a1a1aa; margin: .35rem 0 .2rem; }
.tr-code { background: #fafafa; border: 1px solid #e4e4e7; border-radius: .5rem; padding: .5rem .65rem; margin: 0;
  font-size: .78rem; white-space: pre-wrap; word-break: break-word; max-height: 16rem; overflow: auto; color: #27272a; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
[data-testid="stExpander"] details { border-radius: .75rem; }
/* Sin el muñequito de "Running…" arriba a la derecha: el avance ya se ve en el chat */
[data-testid="stStatusWidget"] { display: none; }
/* Sidebar estilo shadcn: header, grupos con label, menú de botones y footer */
[data-testid="stSidebar"] { border-right: 1px solid #e4e4e7; }
[data-testid="stSidebarUserContent"] { padding-top: .5rem; }
.sb-head { display: flex; align-items: center; gap: .6rem; padding: .25rem .25rem .75rem; }
.sb-logo { width: 2rem; height: 2rem; border-radius: .5rem; background: #18181b; color: #fafafa;
  display: grid; place-items: center; flex: none; }
.sb-logo svg { width: 1.1rem; height: 1.1rem; }
.sb-head b { display: block; font-size: .9rem; font-weight: 600; color: #09090b; line-height: 1.2; }
.sb-head small { font-size: .75rem; color: #71717a; }
.sb-label { font-size: .75rem; font-weight: 500; color: #71717a; padding: .75rem .5rem .25rem; }
.sb-item { display: flex; align-items: center; gap: .5rem; padding: .35rem .5rem; border-radius: .5rem;
  font-size: .875rem; color: #27272a; text-decoration: none; }
a.sb-item:hover { background: #f4f4f5; color: #09090b; }
.sb-item svg { width: 1rem; height: 1rem; flex: none; color: #71717a; }
.sb-badge { margin-left: auto; font-size: .7rem; font-weight: 500; color: #71717a; background: #f4f4f5;
  border-radius: 9999px; padding: 0 .45rem; line-height: 1.25rem; }
.sb-sub { margin: 0 0 .25rem 1rem; padding-left: .6rem; border-left: 1px solid #e4e4e7; }
.sb-sub div { font-size: .8rem; color: #71717a; padding: .2rem .5rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
[data-testid="stSidebar"] button[kind="tertiary"] { width: 100%; justify-content: flex-start; padding: .35rem .5rem;
  border-radius: .5rem; color: #27272a; min-height: 0; }
[data-testid="stSidebar"] button[kind="tertiary"] p { font-size: .875rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
[data-testid="stSidebar"] button[kind="tertiary"]:hover { background: #f4f4f5; color: #09090b; }
[data-testid="stSidebar"] button[kind="tertiary"]:disabled { background: #f4f4f5; color: #09090b; font-weight: 500; opacity: 1; }
[data-testid="stSidebar"] [data-testid="stElementContainer"] { margin-bottom: -0.6rem; }
[data-testid="stSidebar"] [data-testid="stExpander"] { margin-top: .75rem; }
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


ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")   # en .gitignore


def render_claves() -> None:
    """Carga las claves en os.environ, así no hace falta ponerlas en el .env."""
    faltan = [v for v in CLAVES if not os.getenv(v) or os.getenv(v).startswith("tu_")]
    with SB_PIE.expander("🔑 Claves", expanded=bool(faltan)):
        with st.form("claves"):
            valores = {v: st.text_input(etiqueta, type="password",
                                        placeholder="faltante" if v in faltan else "cargada (vacío = no cambiar)")
                       for v, etiqueta in CLAVES.items()}
            recordar = st.checkbox("Recordar en este equipo", value=True,
                                   help="Las guarda en el .env local (no se sube a git), "
                                        "así no hay que cargarlas cada vez que se reinicia la app.")
            if st.form_submit_button("Guardar"):
                from dotenv import set_key

                for v, valor in valores.items():
                    if valor.strip():
                        os.environ[v] = valor.strip()
                        if recordar:
                            set_key(ENV_FILE, v, valor.strip())
                recargar_agente()


def recargar_agente() -> None:
    """Reimporta el agente para que tome las claves o el modelo nuevos."""
    # ponytail: los módulos leen el entorno al importarse; se reimportan para tomarlo.
    # La conexión MCP previa al campus queda viva hasta reiniciar la app.
    for m in list(sys.modules):
        if m.split(".")[0] in ("agente", "tools", "llm", "campus"):
            del sys.modules[m]
    st.session_state.pop("config", None)
    st.rerun()


# Modelos gratis de OpenRouter con tool calling. Los que no se eligen quedan de respaldo ante un 429.
MODELOS = {
    "google/gemma-4-31b-it:free": "Gemma 4 31B",
    "google/gemma-4-26b-a4b-it:free": "Gemma 4 26B",
    "baidu/ernie-5.5-flash-preview:free": "ERNIE 5.5 Flash",
    "nvidia/nemotron-3.5-super-vl:free": "Nemotron 3.5 Super",
    "thinkingmachines/inkling-2-small:free": "Inkling 2 Small",
}


def render_modelo() -> None:
    """Selector de modelo (el Model Selector del Prompt Input de shadcn, sin el effort)."""
    actual = os.getenv("MODEL_NAME", "google/gemma-4-31b-it:free")
    opciones = list(MODELOS) if actual in MODELOS else [actual, *MODELOS]
    elegido = SB_PIE.selectbox("Modelo", opciones, index=opciones.index(actual),
                               format_func=lambda m: MODELOS.get(m, m),
                               help="Gratis en OpenRouter. Si da 429, se prueban los respaldos.")
    if elegido != actual:
        from dotenv import set_key

        os.environ["MODEL_NAME"] = elegido
        set_key(ENV_FILE, "MODEL_NAME", elegido)   # local: se recuerda al reiniciar
        recargar_agente()


# Estructura del Sidebar de shadcn: header (marca), content (grupos) y footer (claves).
# Los contenedores se crean en orden y se llenan después: las claves van al pie aunque
# se dibujen antes de importar el agente (si faltan, el import falla).
SB_CABECERA, SB_CONTENIDO, SB_PIE = st.sidebar.container(), st.sidebar.container(), st.sidebar.container()
SB_CABECERA.markdown(
    '<div class="sb-head"><div class="sb-logo"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M21.42 10.92a1 1 0 0 0-.02-1.84L12.83 5.18a2 2 0 0 0-1.66 0L2.6 9.08a1 1 0 0 0 0 1.83l8.57 3.91a2 2 0 0 0 1.66 0z"/>'
    '<path d="M22 10v6M6 12.5V16a6 3 0 0 0 12 0v-3.5"/></svg></div>'
    '<div><b>Generador estud.ia</b><small>UTN FRM</small></div></div>', unsafe_allow_html=True)
render_modelo()
render_claves()

try:
    from agente import (
        list_threads, new_thread, resume_thread, saved_turns, select_thread, stream_turn,
    )
    from tools import materias_con_bibliografia
except RuntimeError as e:   # p. ej. faltan claves en el .env
    st.error(str(e))
    st.stop()

ESTUDIA_URL = "https://estud-ia-blond.vercel.app/"
def init_session(config) -> None:
    st.session_state.config = config
    st.session_state.historial = saved_turns(config)   # lista de (pedido, eventos)


# Verbos de cada tool: (en curso, terminó, falló). Siguen los estados del componente Tool.
VERBOS = {
    "buscar_bibliografia": ("Buscando en", "Buscó en", "Falló la búsqueda en"),
    "explorar_campus": ("Consultando", "Consultó", "Falló al consultar"),
    "buscar_en_internet": ("Buscando en", "Buscó en", "Falló la búsqueda en"),
    "generar_banco_estudia": ("Generando", "Generó", "Falló al generar"),
}
# Color de cada tool (clase CSS). Sin rojos, naranjas ni amarillos: los cálidos quedan
# solo para los errores, así un fallo se distingue de un vistazo.
COLOR_TOOL = {"buscar_bibliografia": "violeta", "explorar_campus": "verde",
              "buscar_en_internet": "azul", "generar_banco_estudia": "grafito"}
ICONO_TOOL = {"buscar_bibliografia": "libro", "explorar_campus": "campus",
              "buscar_en_internet": "globo", "generar_banco_estudia": "banco"}


def objeto_llamada(e: dict) -> str:
    """Lo que hace la tool, sin verbo: «la bibliografía de X: "consulta"»."""
    nombre, args = e["nombre"], e["args"]
    if nombre == "buscar_bibliografia":
        return f"la bibliografía de {args.get('materia', '?')}: «{args.get('consulta', '')}»"
    if nombre == "explorar_campus":
        accion = args.get("accion", "")
        if accion == "leer":
            desde = f" (desde el carácter {args['desde']})" if args.get("desde") else ""
            return f"el campus: leer {args.get('url', '')}{desde}"
        if accion == "contenido":
            return f"el campus: contenido del curso {args.get('course_id', '')}"
        return "el campus: materias"
    if nombre == "buscar_en_internet":
        return f"internet: «{args.get('consulta', '')}»"
    if nombre == "generar_banco_estudia":
        tipos = ", ".join(args.get("tipos") or []) or "tipos mezclados"
        return f"el banco: {args.get('cantidad', 10)} preguntas ({tipos})"
    return nombre


def fallo(llamada: dict, r: dict) -> bool:
    nombre, datos, texto = llamada["nombre"], r["datos"], r["texto"]
    if nombre == "buscar_bibliografia":
        return not (datos or {}).get("fragmentos")
    if nombre == "generar_banco_estudia":
        return not (isinstance(datos, dict) and datos.get("ok"))
    return texto.startswith(("No se pudo", "El campus no", "Falta", "La búsqueda web falló"))


def salida_tool(llamada: dict, r: dict) -> str:
    """Cuerpo desplegable: entrada (args) y salida, como bloques de código."""
    from html import escape

    nombre, datos = llamada["nombre"], r["datos"]
    if nombre == "buscar_bibliografia" and (datos or {}).get("fragmentos"):
        salida = "\n".join(f"📄 {f['libro']}, págs. {f['paginas']} (similitud {f['similitud']}): {f['vista']}…"
                           for f in datos["fragmentos"])
    elif nombre == "generar_banco_estudia" and isinstance(datos, dict) and datos.get("ok"):
        salida = (f"✅ {datos['preguntas']} preguntas válidas en {datos['intentos']} intento(s).\n"
                  "Fuentes usadas:\n" + "\n".join(f"- {f}" for f in datos["fuentes"]))
    else:
        texto = r["texto"]
        salida = texto[:2000] + ("…" if len(texto) > 2000 else "")
    entrada = json.dumps(llamada["args"], ensure_ascii=False, indent=2)
    return (f'<div class="tr-label">Entrada</div><pre class="tr-code">{escape(entrada)}</pre>'
            f'<div class="tr-label">Salida</div><pre class="tr-code">{escape(salida)}</pre>')


def html_tool(llamada: dict, r: dict | None = None) -> str:
    """Línea de una tool: girando mientras corre; al terminar, desplegable con la salida."""
    from html import escape

    corriendo, terminado, fallido = VERBOS.get(llamada["nombre"], ("Ejecutando", "Ejecutó", "Falló"))
    objeto = escape(objeto_llamada(llamada))
    ico = icono(ICONO_TOOL.get(llamada["nombre"], "archivo"))
    color = f' tr-{COLOR_TOOL.get(llamada["nombre"], "gris")}'
    if r is None:
        return (f'<div class="tr{color}"><div class="tr-line tr-running" role="status">'
                f'<span class="tr-spin" aria-hidden="true"></span><span>{corriendo} {objeto}</span></div></div>')
    error = fallo(llamada, r)
    clase, verbo = (" tr-error", fallido) if error else (color, terminado)
    return (f'<details class="tr{clase}"><summary>{icono("error") if error else ico}<span>{verbo} {objeto}</span>'
            f'{icono("chevron", "tr-chev")}</summary><div class="tr-body">{salida_tool(llamada, r)}</div></details>')


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


ICONOS = {   # trazos de lucide, en línea para no sumar dependencias
    "globo": '<circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20M2 12h20"/>',
    "libro": '<path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20"/>',
    "archivo": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/>',
    "check": '<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>',
    "flecha": '<path d="M7 7h10v10M7 17 17 7"/>',
    "chevron": '<path d="m9 18 6-6-6-6"/>',
    "campus": '<path d="M21.42 10.92a1 1 0 0 0-.02-1.84L12.83 5.18a2 2 0 0 0-1.66 0L2.6 9.08a1 1 0 0 0 0 1.83l8.57 3.91a2 2 0 0 0 1.66 0z"/><path d="M22 10v6M6 12.5V16a6 3 0 0 0 12 0v-3.5"/>',
    "banco": '<path d="M13 5h8M13 12h8M13 19h8m-18-2 2 2 4-4M3 7l2 2 4-4"/>',
    "error": '<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6m0-6 6 6"/>',
}


def icono(nombre: str, clase: str = "") -> str:
    return (f'<svg class="tr-ico {clase}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
            f"{ICONOS[nombre]}</svg>")


def html_razonamiento(texto: str) -> str:
    """Línea "Pensó" que se despliega con el razonamiento (Reasoning de shadcn)."""
    from html import escape

    return (f'<details class="tr tr-pensar"><summary>{icono("check")}<span>Pensó</span>'
            f'{icono("chevron", "tr-chev")}</summary><div class="tr-body tr-pre">{escape(texto.strip())}</div></details>')


def fuentes_del_turno(eventos: list[dict]) -> list[dict]:
    """Fuentes que usó el agente, sin repetir: web (con link), campus y libros."""
    llamadas = {e["id"]: e for e in eventos if e["tipo"] == "llamada"}
    fuentes = {}
    for e in eventos:
        if e["tipo"] != "resultado" or e["id"] not in llamadas:
            continue
        nombre, args, datos = llamadas[e["id"]]["nombre"], llamadas[e["id"]]["args"], e["datos"]
        if nombre == "buscar_en_internet" and isinstance(datos, list):
            for f in datos:
                if f.get("url"):
                    fuentes[f["url"]] = {"titulo": f.get("titulo"), "url": f["url"], "icono": "globo"}
        elif nombre == "explorar_campus" and isinstance(datos, dict) and datos.get("archivo"):
            fuentes[args.get("url") or datos["archivo"]] = {
                "titulo": datos["archivo"], "url": args.get("url"), "icono": "archivo"}
        elif nombre == "buscar_bibliografia" and isinstance(datos, dict):
            for f in datos.get("fragmentos", []):
                fuentes.setdefault(f["libro"], {"titulo": f["libro"], "url": None, "icono": "libro"})
    return list(fuentes.values())


def html_fuentes(fuentes: list[dict]) -> str:
    """Línea "Usó N fuentes" que se despliega con un link por fuente (Sources de shadcn)."""
    from html import escape
    from urllib.parse import urlparse

    filas = []
    for f in fuentes:
        titulo = escape(f["titulo"] or urlparse(f["url"]).hostname or f["url"])
        if f["url"]:
            filas.append(f'<a class="tr-line" href="{escape(f["url"])}" target="_blank" rel="noreferrer">'
                         f'{icono(f["icono"])}<span>{titulo}<span class="sr-only"> (se abre en otra pestaña)'
                         f'</span></span>{icono("flecha", "tr-out")}</a>')
        else:
            filas.append(f'<div class="tr-line">{icono(f["icono"])}<span>{titulo}</span></div>')
    n = len(fuentes)
    return (f'<details class="tr"><summary>{icono("globo")}<span>Usó {n} fuente{"s" if n != 1 else ""}</span>'
            f'{icono("chevron", "tr-chev")}</summary><div class="tr-body">{"".join(filas)}</div></details>')


def render_eventos(eventos) -> list[dict]:
    """Dibuja los eventos a medida que llegan (en vivo o desde el historial)."""
    emitidos = []
    llamadas = {}   # id del tool call -> (evento de la llamada, línea que se actualiza en el lugar)
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
            st.markdown(html_razonamiento(e["texto"]), unsafe_allow_html=True)
        elif tipo == "recordatorio":
            st.info("🔁 El modelo terminó sin generar el banco: el middleware le recordó el paso 4.")
        elif tipo == "texto":
            if render_banco(e["texto"], f"{id(e)}-{len(emitidos)}"):
                if plan:   # el banco cierra el turno: el plan queda completo
                    plan.markdown(html_plan(pasos, terminado=True), unsafe_allow_html=True)
            else:
                st.markdown(e["texto"])
        elif tipo == "llamada":
            linea = st.empty()   # "en curso" hasta que llega el resultado
            linea.markdown(html_tool(e), unsafe_allow_html=True)
            llamadas[e["id"]] = (e, linea)
        elif tipo == "resultado" and e["id"] in llamadas:
            llamada, linea = llamadas[e["id"]]
            linea.markdown(html_tool(llamada, e), unsafe_allow_html=True)
    resueltas = {e["id"] for e in emitidos if e["tipo"] == "resultado"}
    for id_llamada, (llamada, linea) in llamadas.items():
        if id_llamada not in resueltas:   # el turno terminó sin su resultado: falló
            linea.markdown(html_tool(llamada, {"datos": None, "texto": "No se pudo completar: el turno terminó antes."}),
                           unsafe_allow_html=True)
    if fuentes := fuentes_del_turno(emitidos):
        st.markdown(html_fuentes(fuentes), unsafe_allow_html=True)
    return emitidos


def render_sidebar() -> None:
    """Content: conversaciones y bibliografía. Footer: modelo y link a estud.ia (las claves ya están)."""
    from html import escape

    actual = st.session_state.config["configurable"]["thread_id"]
    with SB_CONTENIDO:
        st.markdown('<div class="sb-label">Conversaciones</div>', unsafe_allow_html=True)
        if st.button("Nueva conversación", icon=":material/add:", type="tertiary",
                     disabled=not st.session_state.historial):
            init_session(new_thread())
            st.rerun()
        for thread_id, titulo in list_threads():
            # la activa va deshabilitada: el CSS la marca como seleccionada (isActive)
            if st.button(titulo, key=thread_id, icon=":material/chat_bubble_outline:", type="tertiary",
                         disabled=thread_id == actual):
                init_session(select_thread(thread_id))
                st.rerun()

        st.markdown('<div class="sb-label">Bibliografía</div>', unsafe_allow_html=True)
        materias = materias_con_bibliografia()
        if not materias:
            st.warning("No hay libros indexados. Ejecutá `python -m rag.ingest`.")
        for materia, libros in materias.items():
            sub = "".join(f"<div title='{escape(l)}'>{escape(l)}</div>" for l in libros)
            st.markdown(f'<div class="sb-item">{icono("libro")}<span>{escape(materia)}</span>'
                        f'<span class="sb-badge">{len(libros)}</span></div><div class="sb-sub">{sub}</div>',
                        unsafe_allow_html=True)
    with SB_PIE:
        st.markdown(f'<a class="sb-item" href="{ESTUDIA_URL}" target="_blank" rel="noreferrer">{icono("flecha")}'
                    f'<span>Abrir estud.ia<span class="sr-only"> (se abre en otra pestaña)</span></span></a>',
                    unsafe_allow_html=True)


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

def leer_adjuntos(archivos, turno: int) -> list[dict]:
    """Texto de los archivos adjuntos, como material del turno (lo lee la generadora)."""
    import tempfile
    from pathlib import Path

    from campus.archivos import extraer_texto
    from tools import MAX_CHARS_CAMPUS

    material = []
    for f in archivos:
        with tempfile.NamedTemporaryFile(suffix=Path(f.name).suffix) as tmp:
            tmp.write(f.getvalue())
            tmp.flush()
            try:
                texto = extraer_texto(Path(tmp.name), MAX_CHARS_CAMPUS)
            except Exception as e:   # noqa: BLE001
                st.warning(f"No se pudo leer «{f.name}»: {e}")
                continue
        if texto.strip():
            material.append({"turno": turno, "origen": "adjunto", "fuente": f"Adjunto: {f.name}", "texto": texto})
        else:
            st.warning(f"«{f.name}» no tiene texto extraíble (¿escaneado?).")
    return material


if entrada := st.chat_input("Pedí preguntas sobre una materia y sus temas…", accept_file="multiple",
                            file_type=["pdf", "pptx", "docx", "txt", "md"]):
    turno = len(st.session_state.historial) + 1
    prompt = entrada.text.strip() or "Armá el banco con el material adjunto."
    if entrada.files:
        prompt += "\n\n📎 Adjuntos (ya están en el material): " + ", ".join(f.name for f in entrada.files)
    st.chat_message("user").markdown(prompt)
    with st.chat_message("assistant"):
        try:
            adjuntos = leer_adjuntos(entrada.files, turno)
            with st.spinner("El agente está trabajando..."):
                eventos = render_eventos(stream_turn(prompt, st.session_state.config, turno, adjuntos))
        except Exception as e:   # noqa: BLE001
            st.error(f"El agente falló: {e}")
            st.stop()
    st.session_state.historial.append((prompt, eventos))
    st.rerun()
