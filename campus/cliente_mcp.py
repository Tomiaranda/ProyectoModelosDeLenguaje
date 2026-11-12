"""Cliente MCP del Campus Virtual UTN FRM.

Levanta el servidor `campus_mcp.py` (https://github.com/ignaw05/campus-utn-frm-mcp)
como subproceso por stdio y le habla con el protocolo MCP. El SDK de MCP es
asíncrono y el agente corre sincrónico (Streamlit / consola), así que la sesión
vive en un event loop propio, en un hilo de fondo, y `llamar()` la usa de forma
bloqueante. El servidor se inicia una sola vez (login al campus incluido) y se
reutiliza en todas las llamadas.

El servidor del campus NO se copia en este repo: se clona aparte (ver README) y
se indica su ruta en CAMPUS_MCP_PATH.
"""
import asyncio
import os
import sys
import threading
from contextlib import AsyncExitStack
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
TIMEOUT_INICIO = 60
TIMEOUT_LLAMADA = 180


class CampusNoConfigurado(RuntimeError):
    pass


class ClienteCampusMCP:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._sesion = None
        self._pila: AsyncExitStack | None = None
        self._lock = threading.Lock()

    # --- configuración -------------------------------------------------------

    @staticmethod
    def _parametros():
        from mcp import StdioServerParameters

        usuario, clave = os.getenv("CAMPUS_USER"), os.getenv("CAMPUS_PASS")
        if not usuario or not clave or usuario.startswith("tu_"):
            raise CampusNoConfigurado(
                "El campus no está configurado (faltan CAMPUS_USER / CAMPUS_PASS en el .env)."
            )
        ruta = Path(os.getenv("CAMPUS_MCP_PATH", "campus-utn-frm-mcp/campus_mcp.py"))
        if not ruta.is_absolute():
            ruta = BASE_DIR / ruta
        if not ruta.exists():
            raise CampusNoConfigurado(
                f"No se encontró el servidor MCP del campus en {ruta} (ver README, CAMPUS_MCP_PATH)."
            )
        descargas = os.getenv("CAMPUS_DESCARGAS", str(BASE_DIR / "descargas_campus"))
        return StdioServerParameters(
            command=sys.executable,          # el mismo Python del entorno virtual
            args=[str(ruta)],
            env={**os.environ, "CAMPUS_USER": usuario, "CAMPUS_PASS": clave,
                 "CAMPUS_DESCARGAS": descargas},
        )

    # --- ciclo de vida ---------------------------------------------------------

    async def _conectar(self) -> None:
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client

        self._pila = AsyncExitStack()
        lectura, escritura = await self._pila.enter_async_context(stdio_client(self._parametros()))
        self._sesion = await self._pila.enter_async_context(ClientSession(lectura, escritura))
        await self._sesion.initialize()

    def _asegurar_conexion(self) -> None:
        with self._lock:
            if self._sesion is not None:
                return
            if self._loop is None:
                self._loop = asyncio.new_event_loop()   # en Windows es Proactor: soporta subprocesos
                threading.Thread(target=self._loop.run_forever, daemon=True).start()
            futuro = asyncio.run_coroutine_threadsafe(self._conectar(), self._loop)
            try:
                futuro.result(timeout=TIMEOUT_INICIO)
            except Exception:
                self._sesion = None
                raise

    # --- uso -------------------------------------------------------------------

    def llamar(self, herramienta: str, argumentos: dict | None = None) -> str:
        """Ejecuta una herramienta del MCP del campus y devuelve su texto."""
        self._asegurar_conexion()
        futuro = asyncio.run_coroutine_threadsafe(
            self._sesion.call_tool(herramienta, argumentos or {}), self._loop
        )
        try:
            resultado = futuro.result(timeout=TIMEOUT_LLAMADA)
        except Exception:
            self._sesion = None   # la próxima llamada vuelve a levantar el servidor
            raise
        texto = "\n".join(
            getattr(bloque, "text", "") for bloque in resultado.content
        ).strip()
        if getattr(resultado, "isError", False):
            raise RuntimeError(texto or "el servidor del campus devolvió un error")
        return texto


campus = ClienteCampusMCP()   # instancia única para todo el proceso
