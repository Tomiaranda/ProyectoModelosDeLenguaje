import shutil
from io import BytesIO
from pathlib import Path

from llm import obtener_embeddings
from rag import store
from rag.ingest import embeber, fragmentar, minutos_estimados


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


def agregar_materiales(materia, archivos, progreso=None) -> list[str]:
    """Indexa PDFs subidos. progreso(fracción 0-1, mensaje) informa el avance a la UI."""
    progreso = progreso or (lambda fraccion, mensaje: None)
    carpeta = store.LIBROS_DIR / store.slug(materia)
    carpeta.mkdir(parents=True, exist_ok=True)
    archivos = list(archivos)
    if not archivos:
        raise ValueError("Elegí al menos un PDF.")
    # 1) Extraer el texto (≈10 % de la barra: en libros grandes también tarda).
    preparados = []
    for n, archivo in enumerate(archivos):
        nombre = Path(archivo.name).name
        if not nombre.lower().endswith(".pdf"):
            raise ValueError(f"«{nombre}» no es un PDF.")
        destino = carpeta / nombre
        if destino.exists():
            raise ValueError(f"Ya existe el material «{nombre}» en {materia}.")
        datos = archivo.getvalue()

        def al_leer(pagina, total, n=n, nombre=nombre):
            progreso(0.1 * (n + pagina / total) / len(archivos),
                     f"Leyendo «{nombre}»: página {pagina} de {total}")

        fragmentos = fragmentar(BytesIO(datos), al_leer)
        if not fragmentos:
            raise ValueError(f"«{nombre}» no tiene texto extraíble (¿es un PDF escaneado?).")
        preparados.append((nombre, datos, fragmentos))

    # 2) Embeddings (el 90 % restante), respetando la cuota gratuita.
    total = sum(len(f) for _, _, f in preparados)
    minutos = minutos_estimados([t for _, _, f in preparados for (_, _, t) in f])
    aviso = f" · ~{minutos:.0f} min por el límite gratuito" if minutos >= 1 else ""
    embeddings = obtener_embeddings()
    con = store.conectar()
    agregados, hechos = [], 0
    try:
        for nombre, datos, fragmentos in preparados:
            def al_embeber(listos, mensaje, base=hechos, nombre=nombre):
                progreso(0.1 + 0.9 * (base + listos) / total, f"«{nombre}» — {mensaje}{aviso}")

            vectores = embeber(embeddings, [f[2] for f in fragmentos], al_embeber)
            store.guardar(con, materia, nombre, fragmentos, vectores)
            (carpeta / nombre).write_bytes(datos)
            agregados.append(nombre)
            hechos += len(fragmentos)
    finally:
        con.close()
    progreso(1.0, "Listo")
    return agregados
