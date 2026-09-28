"""Extracción de entidades (personas, lugares, organizaciones, proyectos) para el grafo.

graph.py crea un nodo `entity:<slug>` por cada entidad del frontmatter `entities`, así dos notas
que mencionan lo mismo quedan conectadas aunque nadie haya escrito un [[link]] entre ellas.
Best-effort, igual que summarize(): sin Ollama o sin modelo de chat devuelve [] y la escritura
sigue normal.
"""
import json
import re

from .summarize import MAX_INPUT_CHARS, generate

PROMPT = ("Listá los nombres propios de personas, lugares, organizaciones, productos o proyectos "
          "mencionados en este texto (como mucho {n}, los más importantes primero). Respondé SOLO "
          "con una lista JSON de strings, por ejemplo [\"Ana Ruiz\", \"Buenos Aires\"]. Si no hay "
          "ninguno, respondé [].\n\nTexto:\n{text}")

# carpetas del vault donde se extraen entidades (no en BRAIN.md ni profile.md)
PREFIXES = ("knowledge/", "memory/")
_DASH = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.+?)\s*$")


def applies(path: str) -> bool:
    return path.startswith(PREFIXES)


def _clean(items) -> list[str]:
    out, seen = [], set()
    for x in items:
        if isinstance(x, dict):  # algunos modelos devuelven [{"name": ...}]
            x = x.get("name") or x.get("entity") or ""
        s = re.sub(r"\s+", " ", str(x)).strip().strip("\"'`*#[]").strip()
        if not (2 <= len(s) <= 60) or s.lower() in seen:
            continue
        seen.add(s.lower())
        out.append(s)
    return out


def parse(raw: str) -> list[str]:
    """Respuesta del modelo → lista de nombres. Tolerante: JSON (lista, o un objeto con una lista
    adentro), un array JSON en medio de texto, o líneas con guión. Si nada sirve, []."""
    raw = (raw or "").strip()
    if not raw:
        return []
    candidates = [raw]
    m = re.search(r"\[.*\]", raw, re.S)
    if m:
        candidates.append(m.group(0))
    for c in candidates:
        try:
            data = json.loads(c)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict):  # {"entities": [...]} y parecidos
            data = next((v for v in data.values() if isinstance(v, list)), [])
        if isinstance(data, list):
            return _clean(data)
    return _clean(m.group(1) for m in map(_DASH.match, raw.splitlines()) if m)


def extract(text: str, max_entities: int = 8) -> list[str]:
    """Nombres de entidades (personas, lugares, organizaciones, proyectos) mencionadas en el
    texto, vía el modelo de chat local de Ollama. Lista vacía si Ollama no responde — best-effort,
    igual que summarize()."""
    text = (text or "").strip()
    if len(text.split()) < 3:
        return []
    try:
        raw = generate(PROMPT.format(n=max_entities, text=text[:MAX_INPUT_CHARS]))
        return parse(raw or "")[:max_entities]
    except Exception:
        return []
