"""Ingesta del RAG: PDFs de la bibliografía -> fragmentos de 2 páginas -> embeddings -> SQLite.

Se ejecuta una vez, y de nuevo cada vez que se agregan o cambian libros:

    python -m rag.ingest                       # todas las materias
    python -m rag.ingest modelos-de-lenguaje   # solo una materia

Cada subcarpeta de rag/libros/ es una materia y cada PDF dentro, un libro.
"""
import sys
import time

from pypdf import PdfReader

from llm import obtener_embeddings
from rag.store import LIBROS_DIR, borrar_materia, conectar, guardar, slug

PAGINAS_POR_FRAGMENTO = 2   # requisito de diseño: un embedding = dos páginas
MAX_CHARS_EMBEDDING = 8000  # gemini-embedding-001 acepta ~2048 tokens por texto
LOTE = 20                   # textos por llamada a la API de embeddings
MIN_CHARS = 80              # pares de páginas casi vacíos (portadas, láminas) se omiten


def fragmentar(pdf) -> list[tuple[int, int, str]]:
    """(pág. desde, pág. hasta, texto) agrupando de a dos páginas."""
    paginas = [(p.extract_text() or "").strip() for p in PdfReader(pdf).pages]
    fragmentos = []
    for i in range(0, len(paginas), PAGINAS_POR_FRAGMENTO):
        grupo = paginas[i:i + PAGINAS_POR_FRAGMENTO]
        texto = "\n\n".join(grupo).strip()
        if len(texto) >= MIN_CHARS:
            fragmentos.append((i + 1, i + len(grupo), texto))
    return fragmentos


def embeber(embeddings, textos: list[str]) -> list[list[float]]:
    """Embeddings por lotes, con espera ante el límite de peticiones (429) del tier gratuito."""
    vectores = []
    for i in range(0, len(textos), LOTE):
        lote = [t[:MAX_CHARS_EMBEDDING] for t in textos[i:i + LOTE]]
        for intento in range(6):
            try:
                vectores.extend(embeddings.embed_documents(lote))
                break
            except Exception as e:   # noqa: BLE001 - el SDK no expone un tipo único para 429
                if intento == 5 or "429" not in str(e) and "RESOURCE_EXHAUSTED" not in str(e):
                    raise
                espera = 20 * (intento + 1)
                print(f"   límite de peticiones; reintento en {espera} s...")
                time.sleep(espera)
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
            print(f"[{materia}] {libro.name}: {len(fragmentos)} fragmentos de 2 páginas")
    con.close()
    print("Ingesta terminada.")


if __name__ == "__main__":
    main()
