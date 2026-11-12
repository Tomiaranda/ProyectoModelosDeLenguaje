"""Extracción de texto de los archivos que descarga el MCP del campus.

El servidor del campus guarda los archivos (PDF, PPTX, DOCX) en disco y solo
devuelve la ruta; el agente necesita el texto de las filminas y guías.
"""
from pathlib import Path


def extraer_texto(ruta: Path, max_chars: int) -> str:
    sufijo = ruta.suffix.lower()
    if sufijo == ".pdf":
        from pypdf import PdfReader

        partes, total = [], 0
        for n, pagina in enumerate(PdfReader(ruta).pages, 1):
            texto = (pagina.extract_text() or "").strip()
            if texto:
                partes.append(f"[pág. {n}] {texto}")
                total += len(texto)
            if total >= max_chars:
                break
        return "\n".join(partes)[:max_chars]
    if sufijo == ".pptx":
        from pptx import Presentation

        partes = []
        for n, diapositiva in enumerate(Presentation(ruta).slides, 1):
            textos = [s.text_frame.text for s in diapositiva.shapes if s.has_text_frame]
            if any(t.strip() for t in textos):
                partes.append(f"[diapositiva {n}] " + " / ".join(t.strip() for t in textos if t.strip()))
        return "\n".join(partes)[:max_chars]
    if sufijo == ".docx":
        from docx import Document

        return "\n".join(p.text for p in Document(ruta).paragraphs if p.text.strip())[:max_chars]
    if sufijo in {".txt", ".md"}:
        return ruta.read_text(encoding="utf-8", errors="replace")[:max_chars]
    raise ValueError(f"formato {sufijo or 'desconocido'} no soportado (se leen PDF, PPTX, DOCX, TXT)")
