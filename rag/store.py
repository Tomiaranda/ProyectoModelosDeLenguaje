"""Base vectorial del RAG en SQLite: bibliografía oficial de cada materia.

Cada fila es un fragmento de EXACTAMENTE dos páginas consecutivas de un libro
(la última puede ser una sola si el libro tiene páginas impares), con su
embedding guardado como BLOB de float32. La búsqueda es por similitud coseno
con numpy: para unos pocos libros por materia (miles de filas) es instantánea y
no hace falta un motor vectorial aparte.

Estructura de carpetas de los libros:
    rag/libros/<materia>/<libro>.pdf     p. ej. rag/libros/modelos-de-lenguaje/...
"""
import difflib
import re
import sqlite3
import unicodedata
from pathlib import Path

import numpy as np

RAG_DIR = Path(__file__).parent
LIBROS_DIR = RAG_DIR / "libros"
DB_PATH = RAG_DIR / "bibliografia.db"

ESQUEMA = """
CREATE TABLE IF NOT EXISTS fragmentos (
    id         INTEGER PRIMARY KEY,
    materia    TEXT NOT NULL,      -- slug de la carpeta, p. ej. modelos-de-lenguaje
    libro      TEXT NOT NULL,      -- nombre del archivo PDF
    pag_desde  INTEGER NOT NULL,   -- numeración del PDF, empieza en 1
    pag_hasta  INTEGER NOT NULL,
    texto      TEXT NOT NULL,
    embedding  BLOB NOT NULL       -- float32
);
CREATE INDEX IF NOT EXISTS idx_materia ON fragmentos(materia);
"""


def slug(texto: str) -> str:
    """'Modelos de Lenguaje' -> 'modelos-de-lenguaje' (sin tildes ni mayúsculas)."""
    ascii_ = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_.lower()).strip("-")


def conectar() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.executescript(ESQUEMA)
    return con


def materias_cargadas(con: sqlite3.Connection) -> dict[str, list[str]]:
    """{materia: [libros]} de lo que ya está indexado."""
    filas = con.execute(
        "SELECT DISTINCT materia, libro FROM fragmentos ORDER BY materia, libro"
    ).fetchall()
    resultado: dict[str, list[str]] = {}
    for materia, libro in filas:
        resultado.setdefault(materia, []).append(libro)
    return resultado


def resolver_materia(con: sqlite3.Connection, materia: str) -> str | None:
    """Encuentra la materia indexada que corresponde a lo que escribió el alumno.

    Acepta variaciones ('Modelos de lenguaje', 'modelos lenguaje', 'MdL'...):
    primero coincidencia exacta del slug, después inclusión y por último difflib.
    """
    disponibles = list(materias_cargadas(con))
    buscada = slug(materia)
    if buscada in disponibles:
        return buscada
    for m in disponibles:
        if buscada and (buscada in m or m in buscada):
            return m
    cercanas = difflib.get_close_matches(buscada, disponibles, n=1, cutoff=0.6)
    return cercanas[0] if cercanas else None


def guardar(con: sqlite3.Connection, materia: str, libro: str,
            fragmentos: list[tuple[int, int, str]], vectores: list[list[float]]) -> None:
    con.executemany(
        "INSERT INTO fragmentos (materia, libro, pag_desde, pag_hasta, texto, embedding) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            (materia, libro, desde, hasta, texto, np.asarray(v, dtype=np.float32).tobytes())
            for (desde, hasta, texto), v in zip(fragmentos, vectores)
        ],
    )
    con.commit()


def borrar_materia(con: sqlite3.Connection, materia: str) -> None:
    con.execute("DELETE FROM fragmentos WHERE materia = ?", (materia,))
    con.commit()


def buscar(con: sqlite3.Connection, materia: str, vector_consulta: list[float],
           k: int = 3) -> list[dict]:
    """Los k fragmentos de la materia más parecidos a la consulta (similitud coseno)."""
    filas = con.execute(
        "SELECT libro, pag_desde, pag_hasta, texto, embedding FROM fragmentos WHERE materia = ?",
        (materia,),
    ).fetchall()
    if not filas:
        return []
    matriz = np.vstack([np.frombuffer(f[4], dtype=np.float32) for f in filas])
    q = np.asarray(vector_consulta, dtype=np.float32)
    similitud = matriz @ q / (np.linalg.norm(matriz, axis=1) * np.linalg.norm(q) + 1e-10)
    mejores = np.argsort(-similitud)[:k]
    return [
        {
            "libro": filas[i][0],
            "pag_desde": filas[i][1],
            "pag_hasta": filas[i][2],
            "texto": filas[i][3],
            "similitud": float(similitud[i]),
        }
        for i in mejores
    ]
