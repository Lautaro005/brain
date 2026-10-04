"""Cambios en el grafo con propuesta, imagen, aprobación y deshacer (v0.03.1).

La IA (el chat o un agente por MCP) no cambia las conexiones del grafo directamente: llama a
`propose_graph_change`, que valida el pedido contra el grafo real y guarda una propuesta con un id, la
versión de los archivos que va a tocar y una imagen (SVG) del grafo con los cambios marcados. El usuario
la ve en el chat (o en la vista Grafo) y la aprueba o la rechaza con un click. Aprobar manda el id, no el
texto del pedido: no se vuelve a interpretar nada. Si los archivos cambiaron entre la propuesta y la
aprobación, la propuesta queda vencida (`stale`) y hay que pedir otra. Aplicar es transaccional (todo o
nada) y deja guardado el contenido anterior para "Deshacer".

Primera entrega (como recomienda el informe): agregar y sacar conexiones, que en brain son las entradas
de `related` del frontmatter. Las conexiones que vienen del texto ([[links]]), de tags o de entidades no
se sacan por acá: se avisa cómo cambiarlas.

data/graph_proposals.json (lock en data/.graph_proposals.lock): las últimas MAX_KEEP propuestas.
"""
import hashlib
import json
import math
import secrets
import time
from datetime import datetime, timezone
from xml.sax.saxutils import escape

from . import graph, history, locks, vault

MAX_CHANGES = 10
MAX_KEEP = 60
MAX_NEIGHBORS = 14
KINDS = ("add_link", "remove_link")


class ProposalError(ValueError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code, self.detail = code, detail


def _path():
    return history.DATA / "graph_proposals.json"


def _lock():
    return locks.locked(history.DATA / ".graph_proposals.lock")


def _load() -> list[dict]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _store(items: list[dict]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(items[-MAX_KEEP:], ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha(text: str | None) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()[:16]


# ---------- resolver nombres contra el grafo ----------

def _index(g: dict) -> tuple[dict, dict]:
    """(nodos de archivo por id, nombre en minúscula → id)."""
    files = {n["id"]: n for n in g["nodes"] if n.get("path") and str(n["path"]).endswith(".md")}
    names: dict[str, str] = {}
    for nid, n in files.items():
        for key in (nid.lower(), nid[:-3].lower(), nid.rsplit("/", 1)[-1][:-3].lower(), str(n.get("label", "")).lower()):
            names.setdefault(key, nid)
    return files, names


def _resolve(name: str, names: dict) -> str | None:
    t = str(name or "").strip().strip("/").lower()
    t = t[2:-2] if t.startswith("[[") and t.endswith("]]") else t
    return names.get(t) or names.get(t + ".md") if t else None


def _related(meta: dict) -> list[str]:
    return graph._as_list(meta.get("related"))


def _ref(target: str, files: dict) -> str:
    """Cómo escribir `target` en related: el nombre del archivo si es único, si no la ruta sin .md."""
    stem = target.rsplit("/", 1)[-1][:-3]
    same = [f for f in files if f.rsplit("/", 1)[-1][:-3].lower() == stem.lower()]
    return stem if len(same) <= 1 else target[:-3]


def _connected(g: dict, a: str, b: str) -> str | None:
    for link in g["links"]:
        if {link["source"], link["target"]} == {a, b}:
            return link["kind"]
    return None


# ---------- proponer ----------

def propose(changes, reason: str = "", origin: str = "agent") -> dict:
    if not isinstance(changes, list) or not changes:
        raise ProposalError("empty", "changes tiene que ser una lista con al menos un cambio.")
    if len(changes) > MAX_CHANGES:
        raise ProposalError("too_many", f"Como mucho {MAX_CHANGES} cambios por propuesta; dividilo en varias.")
    g = graph.build_graph()
    files, names = _index(g)
    out, seen, errors = [], set(), []
    for i, ch in enumerate(changes, 1):
        if not isinstance(ch, dict):
            errors.append(f"cambio {i}: tiene que ser un objeto {{type, source, target}}")
            continue
        kind = str(ch.get("type") or ch.get("operation") or "").strip()
        kind = {"add_edge": "add_link", "remove_edge": "remove_link", "add": "add_link", "remove": "remove_link"}.get(kind, kind)
        if kind not in KINDS:
            errors.append(f"cambio {i}: type tiene que ser add_link o remove_link")
            continue
        src, dst = _resolve(ch.get("source"), names), _resolve(ch.get("target"), names)
        if not src:
            errors.append(f"cambio {i}: no existe la nota «{ch.get('source')}»")
            continue
        if not dst:
            errors.append(f"cambio {i}: no existe la nota «{ch.get('target')}»")
            continue
        if src == dst:
            errors.append(f"cambio {i}: origen y destino son la misma nota")
            continue
        if src.startswith("memory/"):
            errors.append(f"cambio {i}: las conexiones de memory/ las calcula brain solo; no se cambian a mano")
            continue
        key = (kind, src, dst)
        if key in seen:
            continue
        seen.add(key)
        meta = vault.parse(vault.read_file(src))[0]
        rel = [r for r in _related(meta) if _resolve(r, names) == dst]
        how = _connected(g, src, dst)
        if kind == "add_link" and (rel or how in ("link", "related")):
            errors.append(f"cambio {i}: {src} ya está conectada con {dst}")
            continue
        if kind == "remove_link" and not rel:
            if how:
                where = {"link": "un link en el texto", "mention": "una mención en el texto", "tag": "un tag compartido",
                         "entity": "una entidad compartida", "folder": "la carpeta", "memory": "la memoria"}.get(how, how)
                errors.append(f"cambio {i}: {src} y {dst} están unidas por {where}, no por related: cambialo en la nota")
            else:
                errors.append(f"cambio {i}: {src} y {dst} no están conectadas")
            continue
        out.append({"type": kind, "source": src, "target": dst, "label": str(ch.get("label") or "")[:60],
                    "source_label": files[src]["label"], "target_label": files[dst]["label"]})
    if errors:
        raise ProposalError("invalid", "; ".join(errors))
    touched = sorted({c["source"] for c in out})
    versions = {p: _sha(vault.read_file(p)) for p in touched}
    pid = f"graph-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
    p = {"id": pid, "status": "pending", "reason": str(reason or "")[:300], "origin": origin, "created_at": _now(),
         "changes": out, "versions": versions, "svg": render_svg(g, out)}
    with _lock():
        items = _load()
        items.append(p)
        _store(items)
    return public(p)


def summary(changes: list[dict]) -> dict:
    add = sum(c["type"] == "add_link" for c in changes)
    nodes = {c["source"] for c in changes} | {c["target"] for c in changes}
    return {"add": add, "remove": len(changes) - add, "nodes": len(nodes)}


def public(p: dict) -> dict:
    out = {k: v for k, v in p.items() if k not in ("svg", "before", "versions")}
    out["summary"] = summary(p["changes"])
    out["preview_url"] = f"/api/graph/proposals/{p['id']}/preview.svg"
    return out


def listing(status: str | None = None) -> list[dict]:
    return [public(p) for p in reversed(_load()) if not status or p["status"] == status]


def get(pid: str) -> dict:
    p = next((x for x in _load() if x["id"] == pid), None)
    if not p:
        raise ProposalError("unknown", pid)
    return p


def preview(pid: str) -> str:
    return get(pid)["svg"]


# ---------- aprobar / rechazar / deshacer ----------

def _apply_related(text: str, src: str, changes: list[dict], files: dict, names: dict) -> list[str]:
    meta = vault.parse(text)[0]
    rel = _related(meta)
    for c in changes:
        if c["type"] == "add_link":
            rel.append(_ref(c["target"], files))
        else:
            rel = [r for r in rel if _resolve(r, names) != c["target"]]
    return rel


def approve(pid: str, by: str = "usuario (dashboard)") -> dict:
    with _lock():
        items = _load()
        p = next((x for x in items if x["id"] == pid), None)
        if not p:
            raise ProposalError("unknown", pid)
        if p["status"] != "pending":
            raise ProposalError("not_pending", p["status"])
        # el grafo cambió desde la propuesta: no se aplica sobre datos distintos de los que vio el usuario
        current = {}
        for path in p["versions"]:
            try:
                current[path] = vault.read_file(path)
            except vault.VaultError:
                current[path] = None
        if any(_sha(current[k]) != v for k, v in p["versions"].items()):
            p["status"] = "stale"
            _store(items)
            raise ProposalError("stale", "Las notas cambiaron después de la propuesta; pedí una nueva.")
        files, names = _index(graph.build_graph())
        before, done = {}, []
        try:
            for path in p["versions"]:
                mine = [c for c in p["changes"] if c["source"] == path]
                new_rel = _apply_related(current[path], path, mine, files, names)
                vault.set_frontmatter(path, {"related": new_rel or None}, op="graph")
                before[path] = current[path]
                done.append(path)
        except Exception as e:
            for path in done:  # todo o nada: se vuelve a lo que había
                vault.write_file(path, before[path], op="restore", stamp_meta=False)
            raise ProposalError("apply_failed", str(e)) from e
        p.update(status="applied", approved_at=_now(), approved_by=by,
                 before=before, after={k: _sha(vault.read_file(k)) for k in before})
        _store(items)
        return public(p)


def reject(pid: str, reason: str = "") -> dict:
    with _lock():
        items = _load()
        p = next((x for x in items if x["id"] == pid), None)
        if not p:
            raise ProposalError("unknown", pid)
        if p["status"] != "pending":
            raise ProposalError("not_pending", p["status"])
        p.update(status="rejected", rejected_at=_now(), reject_reason=str(reason or "")[:300])
        _store(items)
        return public(p)


def undo(pid: str) -> dict:
    with _lock():
        items = _load()
        p = next((x for x in items if x["id"] == pid), None)
        if not p:
            raise ProposalError("unknown", pid)
        if p["status"] != "applied":
            raise ProposalError("not_applied", p["status"])
        for path, sha in (p.get("after") or {}).items():
            try:
                now = vault.read_file(path)
            except vault.VaultError:
                now = None
            if _sha(now) != sha:
                raise ProposalError("changed", f"{path} cambió después de aplicar: deshacelo desde su historial.")
        for path, text in (p.get("before") or {}).items():
            vault.write_file(path, text, op="restore", stamp_meta=False)
        p.update(status="undone", undone_at=_now())
        _store(items)
        return public(p)


# ---------- solo lectura ----------

def inspect(nodes: list[str]) -> dict:
    """Nodos pedidos + sus vecinos y cómo están unidos (para inspect_graph_region)."""
    g = graph.build_graph()
    files, names = _index(g)
    by_id = {n["id"]: n for n in g["nodes"]}
    out, missing = [], []
    for name in (nodes or [])[:10]:
        nid = _resolve(name, names)
        if not nid:
            missing.append(str(name))
            continue
        neigh = []
        for link in g["links"]:
            if nid in (link["source"], link["target"]):
                other = link["target"] if link["source"] == nid else link["source"]
                o = by_id.get(other, {})
                neigh.append({"id": other, "label": o.get("label", other), "kind": o.get("kind"), "via": link["kind"]})
        meta = vault.parse(vault.read_file(nid))[0]
        out.append({"id": nid, "label": files[nid]["label"], "description": files[nid].get("description", ""),
                    "related": _related(meta), "tags": graph._as_list(meta.get("tags")), "neighbors": neigh})
    return {"nodes": out, "missing": missing}


# ---------- imagen ----------
# SVG propio (no un render del D3 del navegador): sale igual en el chat, en la vista Grafo y para un agente,
# y corresponde exactamente a la versión del grafo con la que se validó la propuesta.

GREEN, RED, INK, MUTED, LINE = "#1a7f37", "#b42318", "#0a0a0a", "#6b6b67", "#cfcfcc"


def _short(s: str, n: int = 22) -> str:
    s = str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


def render_svg(g: dict, changes: list[dict], width: int = 640, height: int = 430) -> str:
    by_id = {n["id"]: n for n in g["nodes"]}
    focus = []
    for c in changes:
        for k in ("source", "target"):
            if c[k] not in focus:
                focus.append(c[k])
    neigh = []
    for link in g["links"]:
        if link["kind"] in ("folder", "tag"):
            continue
        for a, b in ((link["source"], link["target"]), (link["target"], link["source"])):
            if a in focus and b not in focus and b not in neigh and len(neigh) < MAX_NEIGHBORS:
                neigh.append(b)
    cx, cy = width / 2, (height - 60) / 2 + 10
    pos = {}
    r1 = 0 if len(focus) == 1 else min(110, 40 + 22 * len(focus))
    for i, nid in enumerate(focus):
        a = 2 * math.pi * i / max(1, len(focus)) - math.pi / 2
        pos[nid] = (cx + r1 * math.cos(a), cy + r1 * math.sin(a))
    for i, nid in enumerate(neigh):
        a = 2 * math.pi * i / max(1, len(neigh)) - math.pi / 2 + 0.3
        pos[nid] = (cx + 255 * math.cos(a), cy + 150 * math.sin(a))
    shown = set(pos)
    changed = {frozenset((c["source"], c["target"])): c["type"] for c in changes}
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
             f'font-family="Archivo, Helvetica, Arial, sans-serif" role="img">',
             f'<rect width="{width}" height="{height}" fill="#ffffff"/>']
    for link in g["links"]:
        a, b = link["source"], link["target"]
        if a in shown and b in shown and frozenset((a, b)) not in changed and link["kind"] not in ("folder", "tag"):
            (x1, y1), (x2, y2) = pos[a], pos[b]
            parts.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{LINE}" stroke-width="1.2"/>')
    for c in changes:
        (x1, y1), (x2, y2) = pos[c["source"]], pos[c["target"]]
        color = GREEN if c["type"] == "add_link" else RED
        parts.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{color}" stroke-width="3" '
                     f'stroke-dasharray="8 6" stroke-linecap="round"/>')
        if c.get("label"):
            parts.append(f'<text x="{(x1 + x2) / 2:.1f}" y="{(y1 + y2) / 2 - 6:.1f}" font-size="11" fill="{color}" '
                         f'text-anchor="middle" paint-order="stroke" stroke="#ffffff" stroke-width="4">'
                         f'{escape(_short(c["label"], 20))}</text>')
    for nid in neigh + focus:
        x, y = pos[nid]
        n = by_id.get(nid, {"label": nid})
        main = nid in focus
        r = 9 if main else 6
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{"#ffffff" if main else "#f3f3f1"}" '
                     f'stroke="{INK if main else MUTED}" stroke-width="{2.5 if main else 1}"/>')
        parts.append(f'<text x="{x:.1f}" y="{y + r + 13:.1f}" font-size="{12 if main else 10}" '
                     f'font-weight="{700 if main else 400}" fill="{INK if main else MUTED}" text-anchor="middle" '
                     f'paint-order="stroke" stroke="#ffffff" stroke-width="4">'
                     f'{escape(_short(n.get("label", nid)))}</text>')
    s = summary(changes)
    legend_y = height - 22
    parts.append(f'<line x1="16" y1="{legend_y - 4}" x2="44" y2="{legend_y - 4}" stroke="{GREEN}" stroke-width="3" stroke-dasharray="8 6"/>')
    parts.append(f'<text x="50" y="{legend_y}" font-size="11" fill="{INK}">conexión nueva · new link</text>')
    parts.append(f'<line x1="210" y1="{legend_y - 4}" x2="238" y2="{legend_y - 4}" stroke="{RED}" stroke-width="3" stroke-dasharray="8 6"/>')
    parts.append(f'<text x="244" y="{legend_y}" font-size="11" fill="{INK}">conexión que se saca · removed</text>')
    parts.append(f'<text x="{width - 16}" y="{legend_y}" font-size="11" fill="{MUTED}" text-anchor="end">'
                 f'+{s["add"]} / −{s["remove"]} · {s["nodes"]} notas</text>')
    parts.append("</svg>")
    return "".join(parts)
