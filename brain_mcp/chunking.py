"""Split de texto largo en chunks que respetan la estructura (conteo de palabras aprox. a tokens).

Orden de prioridades (informe de formato, v0.03.1): primero las unidades de sentido y recién después el
tamaño. Un bloque es un párrafo, una lista, una tabla o un bloque de código entero (los ``` no se cortan
por las líneas en blanco de adentro). Un título nunca queda separado de su primer bloque, y cada chunk que
empieza a mitad de una sección lleva el título de esa sección adelante, así el fragmento se entiende solo
y la búsqueda sabe de qué parte habla. El límite de palabras es la protección final: un bloque gigante
se parte en pedazos de max_words.
"""
import re

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


def _blocks(text: str) -> list[tuple[str, str]]:
    """[(tipo, texto)] con tipo "heading" | "block"."""
    out: list[tuple[str, str]] = []
    cur: list[str] = []
    fence = None

    def flush():
        if cur and "\n".join(cur).strip():
            out.append(("block", "\n".join(cur).strip()))
        cur.clear()

    for line in text.splitlines():
        m = _FENCE.match(line)
        if fence:
            cur.append(line)
            if m and m.group(1) == fence:
                fence = None
                flush()
            continue
        if m:
            flush()
            fence = m.group(1)
            cur.append(line)
            continue
        if _HEADING.match(line):
            flush()
            out.append(("heading", line.strip()))
            continue
        if not line.strip():
            flush()
            continue
        cur.append(line)
    flush()
    return out


def chunk_sections(text: str, max_words: int = 500, overlap: int = 50) -> list[dict]:
    """[{text, section}] — section es el título (sin #) de la sección donde empieza el chunk ("" si no hay)."""
    units: list[tuple[str, str, list[str]]] = []  # (sección, título pendiente, palabras)
    section, pending = "", ""
    for kind, block in _blocks(text):
        if kind == "heading":
            if pending:  # dos títulos seguidos: el primero va pegado al segundo
                units.append((section, "", pending.split()))
            section, pending = _HEADING.match(block).group(2).strip(), block
            continue
        words = (pending + "\n" + block).split() if pending else block.split()
        pending = ""
        for i in range(0, len(words), max_words):
            units.append((section, "", words[i:i + max_words]))
    if pending:
        units.append((section, "", pending.split()))

    chunks: list[dict] = []
    current: list[str] = []
    cur_section = ""
    for sec, _, words in units:
        if current and len(current) + len(words) > max_words:
            chunks.append({"text": " ".join(current), "section": cur_section})
            current = current[-overlap:] if overlap else []
            cur_section = sec
            # el chunk nuevo arranca a mitad de una sección: lleva su título adelante
            if sec and not (words and words[0].startswith("#")):
                current = [f"[{sec}]"] + current
        if not current:
            cur_section = sec
        current = current + words
    if current:
        chunks.append({"text": " ".join(current), "section": cur_section})
    return chunks


def chunk_text(text: str, max_words: int = 500, overlap: int = 50) -> list[str]:
    return [c["text"] for c in chunk_sections(text, max_words, overlap)]
