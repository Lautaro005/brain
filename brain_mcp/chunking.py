"""Split de texto largo en chunks por párrafos (conteo de palabras aprox. a tokens)."""


def chunk_text(text: str, max_words: int = 500, overlap: int = 50) -> list[str]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    # párrafos gigantes se parten en pedazos de max_words
    units: list[list[str]] = []
    for p in paragraphs:
        words = p.split()
        for i in range(0, len(words), max_words):
            units.append(words[i : i + max_words])

    chunks: list[str] = []
    current: list[str] = []
    for words in units:
        if current and len(current) + len(words) > max_words:
            chunks.append(" ".join(current))
            current = current[-overlap:] if overlap else []
        current = current + words
    if current:
        chunks.append(" ".join(current))
    return chunks
