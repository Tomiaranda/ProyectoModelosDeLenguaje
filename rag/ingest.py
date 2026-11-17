"""Ingesta del RAG: PDFs de la bibliografía -> fragmentos de 2 páginas -> embeddings -> SQLite.

Se ejecuta una vez, y de nuevo cada vez que se agregan o cambian libros:

    python -m rag.ingest                       # todas las materias
    python -m rag.ingest modelos-de-lenguaje   # solo una materia

Cada subcarpeta de rag/libros/ es una materia y cada PDF dentro, un libro.
"""
import os
import sys
import time
from collections import deque
from typing import Callable

from pypdf import PdfReader

from llm import obtener_embeddings
from rag.store import LIBROS_DIR, borrar_materia, conectar, guardar, slug

PAGINAS_POR_FRAGMENTO = 2   # requisito de diseño: un embedding = dos páginas
MAX_CHARS_EMBEDDING = 8000  # gemini-embedding-001 acepta ~2048 tokens por texto
LOTE = 20                   # textos por llamada a la API de embeddings (máximo)
MIN_CHARS = 80              # pares de páginas casi vacíos (portadas, láminas) se omiten
# Cuota por minuto del tier gratuito de gemini-embedding-001. Si tu cuenta tiene otra, cambiala en el .env.
CUOTA_TPM = int(os.getenv("EMBEDDINGS_TPM", "30000"))   # tokens por minuto
CUOTA_RPM = int(os.getenv("EMBEDDINGS_RPM", "100"))     # textos por minuto
CHARS_POR_TOKEN = 3.5       # estimación conservadora para castellano

# progreso(fragmentos_hechos, mensaje): lo usa la UI para la barra; por consola se imprime.
Progreso = Callable[[int, str], None]


def fragmentar(pdf, al_leer: Callable[[int, int], None] | None = None) -> list[tuple[int, int, str]]:
    """(pág. desde, pág. hasta, texto) agrupando de a dos páginas.

    al_leer(página, total) se llama después de cada página, porque en libros grandes
    la extracción de texto también tarda.
    """
    lector = PdfReader(pdf)
    total = len(lector.pages)
    paginas = []
    for n, pagina in enumerate(lector.pages, 1):
        paginas.append((pagina.extract_text() or "").strip())
        if al_leer:
            al_leer(n, total)
    fragmentos = []
    for i in range(0, len(paginas), PAGINAS_POR_FRAGMENTO):
        grupo = paginas[i:i + PAGINAS_POR_FRAGMENTO]
        texto = "\n\n".join(grupo).strip()
        if len(texto) >= MIN_CHARS:
            fragmentos.append((i + 1, i + len(grupo), texto))
    return fragmentos


def tokens_estimados(texto: str) -> int:
    return int(len(texto[:MAX_CHARS_EMBEDDING]) / CHARS_POR_TOKEN) + 1


def minutos_estimados(textos: list[str]) -> float:
    """Minutos que lleva embeber los textos respetando la cuota gratuita."""
    tokens = sum(tokens_estimados(t) for t in textos)
    return max(tokens / CUOTA_TPM, len(textos) / CUOTA_RPM)


def _lotes(textos: list[str]):
    """Lotes de hasta LOTE textos que, sumados, entran en la mitad de la cuota por minuto."""
    lote, tokens = [], 0
    for t in textos:
        tk = tokens_estimados(t)
        if lote and (len(lote) == LOTE or tokens + tk > CUOTA_TPM // 2):
            yield lote, tokens
            lote, tokens = [], 0
        lote.append(t[:MAX_CHARS_EMBEDDING])
        tokens += tk
    if lote:
        yield lote, tokens


def _esperar(segundos: float, hechos: int, motivo: str, progreso: Progreso) -> None:
    fin = time.monotonic() + segundos
    while (resta := fin - time.monotonic()) > 0:
        progreso(hechos, f"{motivo}: sigo en {int(resta) + 1} s…")
        time.sleep(min(1.0, resta))


def _imprimir(hechos: int, mensaje: str) -> None:
    print(f"\r   {mensaje:<70}", end="", flush=True)   # una sola línea que se va actualizando


def embeber(embeddings, textos: list[str], progreso: Progreso | None = None) -> list[list[float]]:
    """Embeddings por lotes, a un ritmo que respeta la cuota gratuita (y reintenta si igual hay 429)."""
    progreso = progreso or _imprimir
    vectores: list[list[float]] = []
    ventana: deque[tuple[float, int, int]] = deque()   # (momento, tokens, textos) del último minuto
    for lote, tokens in _lotes(textos):
        # Pausa preventiva: que el último minuto más este lote no supere la cuota.
        while True:
            ahora = time.monotonic()
            while ventana and ahora - ventana[0][0] >= 60:
                ventana.popleft()
            usados_tk = sum(v[1] for v in ventana)
            usados_rq = sum(v[2] for v in ventana)
            if not ventana or (usados_tk + tokens <= CUOTA_TPM and usados_rq + len(lote) <= CUOTA_RPM):
                break
            _esperar(60 - (ahora - ventana[0][0]) + 0.5, len(vectores),
                     "Pausa por el límite gratuito de Google", progreso)
        for intento in range(6):
            try:
                vectores.extend(embeddings.embed_documents(lote))
                break
            except Exception as e:   # noqa: BLE001 - el SDK no expone un tipo único para 429
                if intento == 5 or "429" not in str(e) and "RESOURCE_EXHAUSTED" not in str(e):
                    raise
                _esperar(20 * (intento + 1), len(vectores),
                         "Google respondió 429 (límite de peticiones)", progreso)
        ventana.append((time.monotonic(), tokens, len(lote)))
        progreso(len(vectores), f"Embeddings: {len(vectores)} de {len(textos)} fragmentos")
    return vectores


def main() -> None:
    filtro = slug(sys.argv[1]) if len(sys.argv) > 1 else None
    carpetas = [c for c in sorted(LIBROS_DIR.iterdir()) if c.is_dir()] if LIBROS_DIR.exists() else []
    if filtro:
        carpetas = [c for c in carpetas if slug(c.name) == filtro]
    if not carpetas:
        raise SystemExit(f"No hay carpetas de materias en {LIBROS_DIR} (ver README).")

    embeddings = obtener_embeddings()
    con = conectar()
    for carpeta in carpetas:
        materia = slug(carpeta.name)
        libros = sorted(carpeta.glob("*.pdf"))
        if not libros:
            print(f"[{materia}] sin PDFs, se omite")
            continue
        borrar_materia(con, materia)   # reindexar desde cero, sin duplicados
        for libro in libros:
            fragmentos = fragmentar(libro)
            vectores = embeber(embeddings, [f[2] for f in fragmentos])
            guardar(con, materia, libro.name, fragmentos, vectores)
            print(f"\r[{materia}] {libro.name}: {len(fragmentos)} fragmentos de 2 páginas")
    con.close()
    print("Ingesta terminada.")


if __name__ == "__main__":
    main()
