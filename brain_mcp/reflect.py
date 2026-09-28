"""Reflect: revisa lo que ya está guardado y SUGIERE arreglos. Nunca escribe nada en el vault.

Se corre a mano desde el dashboard (botón "Reflect" en Perfil). Cada sugerencia trae la tool que
habría que llamar para aplicarla; el usuario la aplica (o se la pide a un agente en el chat).

- entidades_faltantes: archivos de knowledge/ o memory/ tocados hace poco que no tienen `entities`
  (se guardaron antes de que existiera la extracción, o Ollama no respondía en ese momento).
- posible_duplicado: el mismo hecho (o casi) en más de un lugar de memory/.
- posible_contradiccion: dos hechos que empiezan igual pero dicen otra cosa ("Vive en Palermo" /
  "Vive en Núñez"): probablemente uno reemplaza al otro y conviene add_memory(..., supersede=...).
"""
from difflib import SequenceMatcher

from . import entities, history, memory, vault
from .summarize import model as llm_model

MAX_ENTITY_FILES = 20  # cada archivo es una llamada al modelo local: se acota por corrida
DUP_RATIO = 0.9      # similitud por caracteres para "es el mismo hecho"
CONTRA_RATIO = 0.6   # similitud por palabras (con las dos primeras iguales) para "habla de lo mismo"


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _missing_entities(use_llm: bool) -> tuple[list[dict], int]:
    out, seen, pending = [], set(), 0
    for ch in history.list_changes(limit=200):
        p = ch["path"]
        if p in seen or not entities.applies(p) or not p.endswith(".md"):
            continue
        seen.add(p)
        try:
            meta, body = vault.parse(vault.read_file(p))
        except vault.VaultError:
            continue  # borrado después
        if meta.get("entities") or len(body.split()) < 5:
            continue
        if not use_llm or len(out) >= MAX_ENTITY_FILES:
            pending += 1
            continue
        ents = entities.extract(body)
        if ents:
            out.append({
                "tipo": "entidades_faltantes", "data": {"path": p, "entities": ents},
                "descripcion": f"{p} no tiene entidades; el modelo local encontró: {', '.join(ents)}.",
                "accion_sugerida": f"set_frontmatter({_q(p)}, {{\"entities\": [{', '.join(map(_q, ents))}]}})",
            })
    return out, pending


def _memory_pairs() -> list[dict]:
    facts = [(c["category"], c["path"], i) for c in memory.list_all() for i in c["items"]]
    out = []
    for a in range(len(facts)):
        for b in range(a + 1, len(facts)):
            ca, pa, fa = facts[a]
            cb, pb, fb = facts[b]
            na, nb = memory._norm(fa), memory._norm(fb)
            ratio = 1.0 if na == nb else SequenceMatcher(None, na, nb).ratio()
            data = {"a": {"category": ca, "path": pa, "fact": fa}, "b": {"category": cb, "path": pb, "fact": fb},
                    "ratio": round(ratio, 2)}
            if ratio >= DUP_RATIO:
                out.append({
                    "tipo": "posible_duplicado", "data": data,
                    "descripcion": f"Mismo hecho en {ca} y {cb}: \"{fa}\" / \"{fb}\".",
                    "accion_sugerida": f"Quitar uno desde Perfil → Tu memoria, o str_replace_file({_q(pb)}, {_q('- ' + fb)}, \"\")",
                })
            elif (na.split()[:2] == nb.split()[:2]
                  and SequenceMatcher(None, na.split(), nb.split()).ratio() >= CONTRA_RATIO):
                out.append({
                    "tipo": "posible_contradiccion", "data": data,
                    "descripcion": f"\"{fa}\" ({ca}) y \"{fb}\" ({cb}) parecen hablar de lo mismo con datos distintos.",
                    "accion_sugerida": f"add_memory({_q(fb)}, {_q(cb)}, supersede={_q(fa)})  # si el vigente es el segundo",
                })
    return out


def run() -> dict:
    """Sugerencias [{tipo, descripcion, accion_sugerida, data}]. No escribe nada."""
    use_llm = llm_model() is not None
    ents, pending = _missing_entities(use_llm)
    suggestions = _memory_pairs() + ents
    return {"suggestions": suggestions, "llm": use_llm, "entities_pending": pending}
