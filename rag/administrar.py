import shutil
from io import BytesIO
from pathlib import Path

from llm import obtener_embeddings
from rag import store
from rag.ingest import embeber, fragmentar


def listar_materias() -> dict[str, list[str]]:
    materias: dict[str, list[str]] = {}
    if store.LIBROS_DIR.exists():
        for carpeta in sorted(store.LIBROS_DIR.iterdir()):
            if carpeta.is_dir():
                materias[carpeta.name] = sorted(p.name for p in carpeta.glob("*.pdf"))
    con = store.conectar()
    try:
        for materia, libros in store.materias_cargadas(con).items():
            actuales = materias.setdefault(materia, [])
            for libro in libros:
                if libro not in actuales:
                    actuales.append(libro)
    finally:
        con.close()
    return {m: sorted(v) for m, v in sorted(materias.items())}


def crear_materia(nombre: str) -> str:
    materia = store.slug(nombre)
    if not materia:
        raise ValueError("El nombre de la materia no puede quedar vacío.")
    carpeta = store.LIBROS_DIR / materia
    if carpeta.exists():
        raise ValueError(f"La materia «{materia}» ya existe.")
    carpeta.mkdir(parents=True)
    return materia


def eliminar_materia(materia: str) -> None:
    con = store.conectar()
    try:
        store.borrar_materia(con, materia)
    finally:
        con.close()
    carpeta = store.LIBROS_DIR / materia
    if carpeta.exists():
        shutil.rmtree(carpeta)


def eliminar_material(materia: str, libro: str) -> None:
    con = store.conectar()
    try:
        store.borrar_libro(con, materia, libro)
    finally:
        con.close()
    archivo = store.LIBROS_DIR / materia / Path(libro).name
    if archivo.exists():
        archivo.unlink()


def agregar_materiales(materia, archivos) -> list[str]:
    carpeta = store.LIBROS_DIR / store.slug(materia)
    carpeta.mkdir(parents=True, exist_ok=True)
    preparados = []
    for archivo in archivos:
        nombre = Path(archivo.name).name
        if not nombre.lower().endswith(".pdf"):
            raise ValueError(f"«{nombre}» no es un PDF.")
        destino = carpeta / nombre
        if destino.exists():
            raise ValueError(f"Ya existe el material «{nombre}» en {materia}.")
        datos = archivo.getvalue()
        fragmentos = fragmentar(BytesIO(datos))
        if not fragmentos:
            raise ValueError(f"«{nombre}» no tiene texto extraíble.")
        preparados.append((nombre, datos, fragmentos))
    if not preparados:
        raise ValueError("Elegí al menos un PDF.")
    embeddings = obtener_embeddings()
    con = store.conectar()
    agregados = []
    try:
        for nombre, datos, fragmentos in preparados:
            vectores = embeber(embeddings, [f[2] for f in fragmentos])
            store.guardar(con, materia, nombre, fragmentos, vectores)
            (carpeta / nombre).write_bytes(datos)
            agregados.append(nombre)
    finally:
        con.close()
    return agregados
