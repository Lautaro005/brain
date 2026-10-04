"""Fact check: verificar afirmaciones con un proceso visible y repetible (v0.03.1).

No es una máquina que decide qué es "verdad". Sigue los pasos del informe de Fact check:
  1. separar las afirmaciones verificables (y decir cuáles no lo son: opiniones, predicciones, vaguedades);
  2. buscar primero en brain (search_knowledge híbrida) y decidir si alcanza: relevancia, fecha, fuente
     primaria, contradicciones y riesgo del tema;
  3. salir a internet solo si hace falta (y si el usuario lo permite), avisándolo, con consultas neutrales,
     de evidencia, de contraevidencia y de la fuente primaria;
  4. abrir y leer cada página (nunca citar solo el fragmento del buscador), clasificarla por tipo
     (oficial, académica, verificador, periodística, referencia, red social) y agrupar las que no son
     independientes (mismo sitio o texto copiado) en una sola "línea de evidencia";
  5. comparar cada fuente con la afirmación (el modelo local de Ollama): apoya, contradice, apoya con límites,
     no responde. La cita que da el modelo tiene que estar en el texto de la página; si no, no cuenta;
  6. el veredicto sale de reglas fijas sobre las líneas de evidencia (no de un porcentaje del modelo):
     respaldada · probablemente respaldada · mixta · no concluyente · probablemente falsa · falsa · no verificable.
Nunca guarda nada en el vault solo: guardar una fuente es un click aparte del usuario (save_url).

Preferencias en data/factcheck.json (la clave de Brave, si se usa, nunca sale por la API: `public()`).
Informes: los últimos MAX_REPORTS en data/factchecks.json.
"""
import json
import logging
import os
import re
import secrets
import time
from datetime import date, datetime, timezone
from urllib.parse import parse_qs, quote_plus, urlparse

import requests

from . import history, locks
from .summarize import generate

log = logging.getLogger(__name__)

MAX_CLAIMS = 5
MAX_REPORTS = 30
MAX_TEXT = 4000           # texto de entrada
EXCERPT_CHARS = 3500      # lo que ve el modelo de cada fuente
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36 brain-factcheck"

LEVELS = {
    "quick": {"min_sources": 1, "max_sources": 3, "require_primary": False, "counterevidence": False},
    "standard": {"min_sources": 2, "max_sources": 6, "require_primary": True, "counterevidence": True},
    "thorough": {"min_sources": 3, "max_sources": 10, "require_primary": True, "counterevidence": True},
}
CLAIM_TYPES = ("numeric", "science_health", "legal_finance", "news", "history", "quote", "product", "other")
RISK_TOPICS = ("health", "law", "finance", "safety")
SOURCE_TYPES = ("official", "academic", "factchecker", "news", "reference", "social", "web")
SOURCE_PREFS = ("prefer", "corroborate", "orient", "lead_only", "exclude")
FRESHNESS = ("topic", "day", "month", "year", "any", "custom")
PROVIDERS = ("duckduckgo", "searxng", "brave")
# antigüedad aceptable por tipo de afirmación cuando la frescura es "según el tema" (días; None = sin límite)
TOPIC_AGE = {"news": 30, "product": 180, "numeric": 365, "legal_finance": 730, "science_health": 1825,
             "history": None, "quote": None, "other": None}
FRESH_DAYS = {"day": 1, "month": 31, "year": 366, "any": None}

DEFAULTS = {
    "enabled": True,                    # la vista Fact check puede salir a internet cuando la memoria no alcanza
    "chat_auto": False,                 # el chat usa fact_check solo, sin que el usuario lo pida
    "level": "standard",
    **LEVELS["standard"],
    "freshness": "topic", "fresh_from": "", "fresh_to": "",
    "claim_types": list(CLAIM_TYPES),
    "always_high_risk": True,           # verificar siempre (y con más rigor) salud, derecho, finanzas, seguridad
    "high_risk_topics": list(RISK_TOPICS),
    "regions": ["AR"], "languages": ["es", "en"], "allow_foreign": True, "prefer_local": True,
    "source_prefs": {"official": "prefer", "academic": "prefer", "factchecker": "prefer", "news": "corroborate",
                     "reference": "orient", "social": "lead_only", "web": "corroborate"},
    "provider": "duckduckgo", "searxng_url": "", "brave_key": "",
    "blocked_domains": [],
    "updated_at": "",
}


class FactCheckError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code, self.detail = code, detail


# ---------- preferencias ----------

def _prefs_path():
    return history.DATA / "factcheck.json"


def _reports_path():
    return history.DATA / "factchecks.json"


def _write_json(p, data, private: bool = False) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    if private:
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
    tmp.replace(p)


def _domain_list(v) -> list[str]:
    out = []
    for x in (v if isinstance(v, list) else str(v or "").replace(",", "\n").splitlines()):
        d = str(x).strip().lower()
        d = urlparse(d).hostname or d if "//" in d else d
        d = d.removeprefix("www.").strip("/ ")
        if d and re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", d) and d not in out:
            out.append(d)
    return out[:200]


def _clean(raw: dict, base: dict) -> dict:
    """Valida un dict de preferencias sobre `base`; lo desconocido se ignora."""
    out = json.loads(json.dumps(base))
    for k in ("enabled", "chat_auto", "require_primary", "counterevidence", "always_high_risk", "allow_foreign", "prefer_local"):
        if isinstance(raw.get(k), bool):
            out[k] = raw[k]
    if raw.get("level") in (*LEVELS, "custom"):
        out["level"] = raw["level"]
    for k, lo, hi in (("min_sources", 1, 5), ("max_sources", 1, 12)):
        try:
            if raw.get(k) is not None:
                out[k] = max(lo, min(hi, int(raw[k])))
        except (TypeError, ValueError):
            pass
    out["max_sources"] = max(out["max_sources"], out["min_sources"])
    if raw.get("freshness") in FRESHNESS:
        out["freshness"] = raw["freshness"]
    for k in ("fresh_from", "fresh_to"):
        if k in raw:
            v = str(raw[k] or "")
            out[k] = v if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) else ""
    if isinstance(raw.get("claim_types"), list):
        out["claim_types"] = [t for t in CLAIM_TYPES if t in raw["claim_types"]]
    if isinstance(raw.get("high_risk_topics"), list):
        out["high_risk_topics"] = [t for t in RISK_TOPICS if t in raw["high_risk_topics"]]
    for k, rx in (("regions", r"[A-Z]{2}"), ("languages", r"[a-z]{2}")):
        if k in raw:
            vals = raw[k] if isinstance(raw[k], list) else str(raw[k] or "").replace(",", " ").split()
            norm = [str(v).strip().upper() if k == "regions" else str(v).strip().lower() for v in vals]
            out[k] = [v for i, v in enumerate(norm) if re.fullmatch(rx, v) and v not in norm[:i]][:8]
    if isinstance(raw.get("source_prefs"), dict):
        for t, v in raw["source_prefs"].items():
            if t in SOURCE_TYPES and v in SOURCE_PREFS:
                out["source_prefs"][t] = v
    if raw.get("provider") in PROVIDERS:
        out["provider"] = raw["provider"]
    if "searxng_url" in raw:
        u = str(raw["searxng_url"] or "").strip().rstrip("/")
        if u and not re.match(r"^https?://", u):
            raise FactCheckError("bad_searxng")
        out["searxng_url"] = u[:300]
    if "brave_key" in raw and raw["brave_key"] is not None:  # "" la borra; sin el campo, queda la anterior
        out["brave_key"] = str(raw["brave_key"]).strip()[:200]
    if "blocked_domains" in raw:
        out["blocked_domains"] = _domain_list(raw["blocked_domains"])
    # un nivel fijo pisa sus cuatro parámetros; "custom" deja los que eligió el usuario
    if out["level"] in LEVELS:
        out.update(LEVELS[out["level"]])
    return out


def prefs() -> dict:
    try:
        raw = json.loads(_prefs_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    try:
        out = _clean(raw, DEFAULTS)
    except FactCheckError:
        out = json.loads(json.dumps(DEFAULTS))
    out["updated_at"] = str(raw.get("updated_at") or "")
    return out


def public(p: dict | None = None) -> dict:
    p = dict(p or prefs())
    p["has_brave_key"] = bool(p.pop("brave_key", ""))
    return p


def save_prefs(data: dict) -> dict:
    cur = prefs()
    if data.get("reset"):
        new = json.loads(json.dumps(DEFAULTS))
        # restaurar los valores recomendados no toca la conexión con el buscador (ni la clave guardada)
        for k in ("provider", "searxng_url", "brave_key"):
            new[k] = cur.get(k, new[k])
    else:
        new = _clean(data, cur)
    if new["provider"] == "brave" and not new.get("brave_key"):
        raise FactCheckError("no_brave_key")
    if new["provider"] == "searxng" and not new.get("searxng_url"):
        raise FactCheckError("no_searxng")
    new["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with locks.locked(history.DATA / ".factcheck.lock"):
        _write_json(_prefs_path(), new, private=True)
    return public(new)


def block_domain(domain: str) -> dict:
    p = prefs()
    p["blocked_domains"] = _domain_list(p["blocked_domains"] + [domain])
    return save_prefs({"blocked_domains": p["blocked_domains"]})


# ---------- informes guardados ----------

def reports() -> list[dict]:
    try:
        data = json.loads(_reports_path().read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def listing() -> list[dict]:
    return [{"id": r["id"], "created_at": r["created_at"], "input": r["input"][:160],
             "verdicts": [c.get("verdict") for c in r.get("claims", [])]} for r in reversed(reports())]


def get_report(rid: str) -> dict:
    r = next((x for x in reports() if x["id"] == rid), None)
    if not r:
        raise FactCheckError("unknown", rid)
    return r


def _save_report(r: dict) -> None:
    with locks.locked(history.DATA / ".factcheck.lock"):
        items = [x for x in reports() if x["id"] != r["id"]] + [r]
        _write_json(_reports_path(), items[-MAX_REPORTS:])


def delete_report(rid: str) -> None:
    with locks.locked(history.DATA / ".factcheck.lock"):
        _write_json(_reports_path(), [x for x in reports() if x["id"] != rid])


# ---------- utilidades ----------

def _json_from(raw: str | None):
    """Primer objeto o lista JSON de la respuesta del modelo (los modelos chicos agregan texto alrededor)."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        pass
    for open_, close in (("{", "}"), ("[", "]")):
        i, j = raw.find(open_), raw.rfind(close)
        if 0 <= i < j:
            try:
                return json.loads(raw[i:j + 1])
            except ValueError:
                continue
    return None


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[“”«»\"'’‘`]", "", str(s or ""))).strip().lower()


def quote_in(quote: str, text: str) -> bool:
    """La cita del modelo está en el texto (ignorando comillas, espacios y mayúsculas). Tiene que tener
    al menos 4 palabras: un fragmento más corto se encuentra en cualquier lado."""
    q = _norm(quote).strip(" .…")
    return len(q.split()) >= 4 and q in _norm(text)


_SLD = {"com", "net", "org", "gob", "gov", "edu", "ac", "co", "mil", "int", "nic"}


def site_of(url: str) -> str:
    """Dominio registrable aproximado: noticias.ejemplo.com.ar → ejemplo.com.ar."""
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    parts = host.split(".")
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in _SLD:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


OFFICIAL = re.compile(r"(^|\.)(gov|gob|mil|gouv|gc\.ca|gov\.[a-z]{2}|gob\.[a-z]{2}|gub\.uy|go\.[a-z]{2})$|"
                      r"(^|\.)(who\.int|un\.org|europa\.eu|oecd\.org|worldbank\.org|imf\.org|paho\.org|unesco\.org|"
                      r"cdc\.gov|nih\.gov|boletinoficial\.gob\.ar|indec\.gob\.ar|bcra\.gob\.ar|ine\.es|boe\.es|"
                      r"census\.gov|federalregister\.gov|legislation\.gov\.uk|eur-lex\.europa\.eu|infoleg\.gob\.ar)$")
ACADEMIC = ("doi.org", "arxiv.org", "pubmed.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov", "nature.com", "science.org",
            "sciencedirect.com", "springer.com", "link.springer.com", "wiley.com", "onlinelibrary.wiley.com",
            "jstor.org", "plos.org", "biorxiv.org", "medrxiv.org", "ssrn.com", "thelancet.com", "nejm.org",
            "bmj.com", "jamanetwork.com", "cochranelibrary.com", "scielo.org", "aclanthology.org", "acm.org",
            "ieee.org", "semanticscholar.org", "frontiersin.org", "mdpi.com", "cell.com", "pnas.org")
FACTCHECKERS = ("chequeado.com", "snopes.com", "politifact.com", "factcheck.org", "fullfact.org", "maldita.es",
                "newtral.es", "colombiacheck.com", "aosfatos.org", "lupa.uol.com.br", "factual.afp.com",
                "verificat.cat", "efeverifica.com", "animalpolitico.com", "leadstories.com", "healthfeedback.org",
                "sciencefeedback.co", "climatefeedback.org", "africacheck.org", "boomlive.in", "teyit.org")
REFERENCE = ("wikipedia.org", "britannica.com", "wikidata.org", "encyclopedia.com", "rae.es", "dictionary.com",
             "merriam-webster.com", "investopedia.com")
SOCIAL = ("twitter.com", "x.com", "facebook.com", "instagram.com", "tiktok.com", "reddit.com", "youtube.com",
          "linkedin.com", "threads.net", "t.me", "medium.com", "substack.com", "blogspot.com", "wordpress.com",
          "quora.com", "pinterest.com", "bsky.app", "mastodon.social", "tumblr.com")
NEWS_HINTS = ("news", "noticias", "diario", "times", "post", "herald", "clarin", "lanacion", "infobae", "pagina12",
              "elpais", "bbc", "reuters", "apnews", "afp", "cnn", "guardian", "nytimes", "washingtonpost", "elmundo",
              "abc.es", "lavanguardia", "perfil.com", "ambito", "cronista", "telam", "dw.com", "france24", "euronews",
              "bloomberg", "ft.com", "wsj.com", "economist", "nbcnews", "cbsnews", "abcnews", "npr.org", "politico")


def _ends(host: str, names) -> bool:
    return any(host == n or host.endswith("." + n) for n in names)


def source_type(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    path = urlparse(url).path.lower()
    if _ends(host, FACTCHECKERS) or re.search(r"/(fact-?check|verificacion|chequeo|hoax|bulo)", path):
        return "factchecker"
    if _ends(host, ACADEMIC) or host.endswith(".edu") or ".edu." in host or host.startswith(("scholar.", "repositorio.", "ri.")):
        return "academic"
    if OFFICIAL.search(host):
        return "official"
    if _ends(host, REFERENCE):
        return "reference"
    if _ends(host, SOCIAL):
        return "social"
    if any(h in host for h in NEWS_HINTS):
        return "news"
    return "web"


def _shingles(text: str, n: int = 8) -> set:
    w = _norm(text).split()[:2500]
    return {" ".join(w[i:i + n]) for i in range(0, max(0, len(w) - n + 1))}


def group_sources(sources: list[dict]) -> None:
    """Asigna `group` a cada fuente: mismo sitio o texto copiado (Jaccard de shingles > 0.4) = misma línea
    de evidencia. Marca `republished` en las que repiten el texto de otra."""
    groups: list[dict] = []
    for s in sources:
        sh = _shingles(s.get("text", "")) if s.get("text") else set()
        site = site_of(s["url"]) if s.get("url") else s.get("path", "")
        hit = None
        for g in groups:
            if site and site in g["sites"]:
                hit = g
                break
            for other in g["shingles"]:
                if sh and other and len(sh & other) / max(1, len(sh | other)) > 0.4:
                    hit, s["republished"] = g, True
                    break
            if hit:
                break
        if hit is None:
            hit = {"id": len(groups) + 1, "sites": set(), "shingles": []}
            groups.append(hit)
        hit["sites"].add(site)
        hit["shingles"].append(sh)
        s["group"] = hit["id"]


def _age_days(d: str | None) -> int | None:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(d or ""))
    if not m:
        m2 = re.match(r"(\d{4})", str(d or ""))
        if not m2:
            return None
        return (date.today() - date(int(m2.group(1)), 12, 31)).days
    try:
        return (date.today() - date(int(m.group(1)), int(m.group(2)), int(m.group(3)))).days
    except ValueError:
        return None


def max_age(p: dict, claim_type: str) -> int | None:
    if p["freshness"] == "topic":
        return TOPIC_AGE.get(claim_type)
    if p["freshness"] == "custom":
        return _age_days(p["fresh_from"]) if p["fresh_from"] else None
    return FRESH_DAYS.get(p["freshness"])


def _too_new(p: dict, d: str | None) -> bool:
    return p["freshness"] == "custom" and bool(p["fresh_to"]) and bool(d) and str(d)[:10] > p["fresh_to"]


def _excerpt(text: str, claim: str, limit: int = EXCERPT_CHARS) -> str:
    """Los párrafos que más palabras comparten con la afirmación (en orden), hasta `limit` caracteres."""
    words = {w for w in _norm(claim).split() if len(w) > 3}
    paras = [x.strip() for x in re.split(r"\n\s*\n|\n", text) if x.strip()]
    scored = sorted(range(len(paras)), key=lambda i: -len(words & set(_norm(paras[i]).split())))
    keep, size = set(), 0
    for i in scored:
        if size + len(paras[i]) > limit and keep:
            break
        keep.add(i)
        size += len(paras[i])
    return "\n".join(paras[i] for i in sorted(keep))[:limit]


# ---------- 1. afirmaciones ----------

CLAIMS_PROMPT = """Separá el texto de abajo en afirmaciones verificables, como un verificador profesional.
Para cada afirmación devolvé:
- "text": la afirmación sola, clara y completa (máximo 40 palabras), en el idioma del texto;
- "type": uno de {types};
- "checkable": true si se puede comprobar con evidencia; false si es una opinión, preferencia, predicción, ficción o algo demasiado vago;
- "why_not": si checkable es false, por qué (una frase corta);
- "risk": "high" si es de salud, derecho, finanzas o seguridad, si no "normal";
- "topic": uno de health, law, finance, safety, other;
- "subclaims": si mezcla varias afirmaciones elementales (por ejemplo un número y una fecha), la lista de cada una; si no, [];
- "query": una búsqueda web corta y neutral para comprobarla;
- "entities": nombres propios, cantidades y fechas clave.
Como mucho {n} afirmaciones, las más importantes. Si no hay ninguna verificable, devolvé las que haya con checkable false.
Respondé SOLO con JSON: {{"claims": [ ... ]}}

Texto:
{text}"""


def _fallback_claims(text: str) -> list[dict]:
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if len(s.split()) >= 4][:3] or [text.strip()]
    return [{"text": s[:400], "type": "other", "checkable": True, "risk": "normal", "topic": "other",
             "subclaims": [], "query": s[:200], "entities": [], "llm": False} for s in sents]


def extract_claims(text: str) -> list[dict]:
    raw = _json_from(generate(CLAIMS_PROMPT.format(types=", ".join(CLAIM_TYPES), n=MAX_CLAIMS, text=text[:MAX_TEXT]), fmt="json"))
    items = raw.get("claims") if isinstance(raw, dict) else raw if isinstance(raw, list) else None
    if not items:
        return _fallback_claims(text)
    out = []
    for c in items[:MAX_CLAIMS]:
        if not isinstance(c, dict) or not str(c.get("text") or "").strip():
            continue
        t = str(c.get("type") or "other")
        topic = str(c.get("topic") or "other")
        out.append({"text": str(c["text"]).strip()[:400], "type": t if t in CLAIM_TYPES else "other",
                    "checkable": c.get("checkable") is not False, "why_not": str(c.get("why_not") or "")[:200],
                    "risk": "high" if c.get("risk") == "high" or topic in RISK_TOPICS else "normal",
                    "topic": topic if topic in RISK_TOPICS else "other",
                    "subclaims": [str(x)[:200] for x in (c.get("subclaims") or []) if str(x).strip()][:4],
                    "query": str(c.get("query") or c["text"])[:200],
                    "entities": [str(x)[:60] for x in (c.get("entities") or [])][:8], "llm": True})
    return out or _fallback_claims(text)


# ---------- 2. memoria local ----------

def local_evidence(claim: dict, k: int = 5) -> tuple[list[dict], str | None]:
    """Fragmentos de brain parecidos a la afirmación, con la fecha y la URL de su fuente."""
    import sys

    from . import vault

    # server.py puede estar corriendo como __main__ (server por stdio): se usa ese, no se importa otra copia
    srv = next((m for m in (sys.modules.get("server"), sys.modules.get("__main__")) if hasattr(m, "search_core")), None)
    if srv is None:
        import server as srv
    try:
        hits, note, _ = srv.search_core(claim["text"], k)
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"
    out, metas = [], {}
    for h in hits:
        path = h.get("source_md_path") or ""
        if path and path not in metas:
            try:
                metas[path] = vault.parse(vault.read_file(path))[0]
            except Exception:
                metas[path] = {}
        m = metas.get(path, {})
        url = h.get("url") or m.get("url") or ""
        when = str(m.get("published") or m.get("scraped_at") or m.get("updated") or "")[:10]
        out.append({"origin": "brain", "path": path, "url": url, "title": str(m.get("title") or m.get("description") or path),
                    "sitename": "brain", "author": str(m.get("author") or ""), "date": when, "text": h["text"],
                    "type": source_type(url) if url else "web", "inspected": True, "score": h.get("score")})
    return out, note


# ---------- 3. búsqueda web ----------

def _lang_region(p: dict) -> tuple[str, str]:
    lang = (p["languages"] or ["es"])[0]
    region = (p["regions"] or [""])[0]
    return lang, region


def _ddg(query: str, p: dict, days: int | None) -> list[dict]:
    lang, region = _lang_region(p)
    data = {"q": query, "kl": f"{region.lower()}-{lang}" if region else "wt-wt"}
    if days is not None:
        data["df"] = "d" if days <= 1 else "w" if days <= 7 else "m" if days <= 31 else "y" if days <= 366 else ""
    r = requests.post("https://html.duckduckgo.com/html/", data=data, headers={"User-Agent": UA}, timeout=15)
    r.raise_for_status()
    from lxml import html as lh

    doc = lh.fromstring(r.text)
    out = []

    def cls(name):
        return f"contains(concat(' ', normalize-space(@class), ' '), ' {name} ')"

    for res in doc.xpath(f"//div[{cls('result')}]"):
        a = res.xpath(f".//a[{cls('result__a')}]")
        if not a:
            continue
        href = a[0].get("href") or ""
        if "duckduckgo.com/l/" in href:
            href = parse_qs(urlparse(href if href.startswith("http") else "https:" + href).query).get("uddg", [""])[0]
        if "duckduckgo.com/y.js" in href or not href.startswith("http"):
            continue  # anuncios
        snip = res.xpath(f".//*[{cls('result__snippet')}]")
        out.append({"url": href, "title": a[0].text_content().strip(), "snippet": snip[0].text_content().strip() if snip else ""})
    return out


def _searxng(query: str, p: dict, days: int | None) -> list[dict]:
    lang, _ = _lang_region(p)
    params = {"q": query, "format": "json", "language": lang}
    if days is not None:
        params["time_range"] = "day" if days <= 1 else "week" if days <= 7 else "month" if days <= 31 else "year"
    r = requests.get(p["searxng_url"] + "/search", params=params, headers={"User-Agent": UA}, timeout=20)
    r.raise_for_status()
    return [{"url": x.get("url", ""), "title": x.get("title", ""), "snippet": x.get("content", ""),
             "date": str(x.get("publishedDate") or "")[:10]} for x in r.json().get("results", []) if x.get("url")]


def _brave(query: str, p: dict, days: int | None) -> list[dict]:
    lang, region = _lang_region(p)
    params = {"q": query, "search_lang": lang, "count": 10}
    if region:
        params["country"] = region.lower()
    if days is not None:
        params["freshness"] = "pd" if days <= 1 else "pw" if days <= 7 else "pm" if days <= 31 else "py"
    r = requests.get("https://api.search.brave.com/res/v1/web/search", params=params, timeout=20,
                     headers={"Accept": "application/json", "X-Subscription-Token": p["brave_key"]})
    r.raise_for_status()
    return [{"url": x.get("url", ""), "title": x.get("title", ""), "snippet": re.sub(r"<[^>]+>", "", x.get("description", "")),
             "date": str(x.get("page_age") or "")[:10]} for x in (r.json().get("web") or {}).get("results", [])]


def search_web(query: str, p: dict, days: int | None = None) -> list[dict]:
    fn = {"duckduckgo": _ddg, "searxng": _searxng, "brave": _brave}[p["provider"]]
    try:
        return fn(query, p, days)
    except requests.RequestException as e:
        raise FactCheckError("search_failed", f"{p['provider']}: {e}") from e


COUNTER = {"es": "desmentido falso refutado", "en": "false debunked refuted", "pt": "falso desmentido"}
EVIDENCE = {"es": "estudio informe oficial datos", "en": "study official report data", "pt": "estudo relatório oficial"}
PRIMARY = {"es": "fuente original documento oficial", "en": "original source official document", "pt": "fonte original documento oficial"}


def queries(claim: dict, p: dict) -> list[tuple[str, str]]:
    """[(familia, consulta)]: neutral, evidencia, contraevidencia (si está activada) y fuente primaria."""
    base = claim.get("query") or claim["text"]
    langs = p["languages"] or ["es"]
    out = [("neutral", base)]
    for lang in langs[:2]:
        out.append(("evidence", f"{base} {EVIDENCE.get(lang, EVIDENCE['en'])}"))
    if p["counterevidence"]:
        out.append(("counter", f"{base} {COUNTER.get(langs[0], COUNTER['en'])}"))
    ents = " ".join(claim.get("entities") or [])
    if p["require_primary"] or claim.get("risk") == "high":
        out.append(("primary", f"{ents or base} {PRIMARY.get(langs[0], PRIMARY['en'])}"))
    return out


PRIORITY = {"prefer": 0, "corroborate": 1, "orient": 2, "lead_only": 3}


def pick_candidates(results: list[tuple[str, dict]], p: dict, limit: int) -> list[dict]:
    """De los resultados de todas las consultas: sin repetidos, sin dominios bloqueados ni excluidos,
    ordenados por la preferencia de cada tipo de fuente, y con a lo sumo dos páginas por sitio."""
    seen, per_site, out = set(), {}, []
    blocked = set(p["blocked_domains"])
    for fam, r in results:
        url = r.get("url", "")
        if not url.startswith(("http://", "https://")):
            continue
        key = url.split("#")[0].rstrip("/")
        site = site_of(url)
        host = (urlparse(url).hostname or "").removeprefix("www.")
        if key in seen or any(host == b or host.endswith("." + b) for b in blocked):
            continue
        t = source_type(url)
        pref = p["source_prefs"].get(t, "corroborate")
        if pref == "exclude":
            continue
        if not p["allow_foreign"] and p["regions"]:
            tld = host.rsplit(".", 1)[-1]
            if len(tld) == 2 and tld.upper() not in p["regions"] and t not in ("academic", "reference"):
                continue
        seen.add(key)
        out.append({**r, "family": fam, "type": t, "pref": pref, "site": site})
    local = {r.lower() for r in p["regions"]}

    def rank(c):
        tld = (urlparse(c["url"]).hostname or "").rsplit(".", 1)[-1]
        return (PRIORITY.get(c["pref"], 1), 0 if (p["prefer_local"] and tld in local) else 1,
                0 if c["family"] in ("primary", "counter") else 1)

    final = []
    for c in sorted(out, key=rank):
        if per_site.get(c["site"], 0) >= 2:
            continue
        per_site[c["site"]] = per_site.get(c["site"], 0) + 1
        final.append(c)
        if len(final) >= limit:
            break
    # la contraevidencia no puede quedar afuera por el orden: si había, entra al menos una
    if p["counterevidence"] and not any(c["family"] == "counter" for c in final):
        extra = next((c for c in out if c["family"] == "counter" and c not in final and per_site.get(c["site"], 0) < 2), None)
        if extra:
            final[-1:] = [extra] if len(final) >= limit else final[-1:] + [extra]
    return final


def fetch(c: dict) -> dict:
    """Abre la página (texto completo + autor y fecha). Si no se puede (o solo funciona con JavaScript), queda
    "no inspeccionada"."""
    from .scrape import scrape_public

    src = {"origin": "web", "url": c["url"], "title": c.get("title", ""), "snippet": c.get("snippet", ""),
           "type": c.get("type") or source_type(c["url"]), "family": c.get("family", ""), "pref": c.get("pref", ""),
           "sitename": site_of(c["url"]), "author": "", "date": c.get("date", ""), "text": "", "inspected": False}
    try:
        # una URL que salió de un buscador: solo direcciones públicas (validadas en cada redirección) y sin
        # ejecutar su JavaScript, así un resultado nunca lleva a brain a la red local
        data = scrape_public(c["url"])
        meta = data.get("meta") or {}
        src.update(text=data["text"][:60000], inspected=True, title=data.get("title") or src["title"],
                   author=meta.get("author", "")[:200], date=(meta.get("date") or src["date"])[:10],
                   sitename=meta.get("sitename") or src["sitename"])
    except Exception as e:
        src["error"] = str(e)[:200]
    return src


# ---------- 5. comparar ----------

STANCE_PROMPT = """Sos un verificador de datos. Compará la AFIRMACIÓN con el TEXTO de una fuente.
Respondé SOLO con JSON:
{{"stance": "supports" | "contradicts" | "supports_with_limits" | "irrelevant",
  "quote": "la oración EXACTA del texto que lo justifica, copiada tal cual (vacío si es irrelevant)",
  "explanation": "una frase simple: qué dato concreto de la fuente apoya o contradice la afirmación",
  "primary": true si la fuente es el documento original del dato (ley, informe oficial, paper, base de datos, declaración directa), si no false,
  "same_scope": true si habla del mismo período, lugar y definición que la afirmación, si no false,
  "event_date": "fecha del hecho que describe la fuente (YYYY-MM-DD o YYYY), vacío si no se sabe"}}
- supports: la fuente dice lo mismo; contradicts: dice lo contrario o un dato distinto;
- supports_with_limits: lo apoya en parte, con condiciones, otro período o un número parecido pero no igual;
- irrelevant: no habla de esto o no alcanza para decidir.
No uses lo que sabés vos: solo el texto.

AFIRMACIÓN: {claim}

TEXTO ({title}):
{text}"""


def stance(claim: dict, src: dict) -> dict:
    body = src["text"] if src.get("inspected") else ""
    if not body.strip():
        return {"stance": "not_inspected", "quote": "", "explanation": "", "counted": False}
    raw = _json_from(generate(STANCE_PROMPT.format(claim=claim["text"], title=src.get("title", "")[:120],
                                                   text=_excerpt(body, claim["text"])), fmt="json"))
    if not isinstance(raw, dict):
        return {"stance": "no_model", "quote": "", "explanation": "", "counted": False}
    st = raw.get("stance") if raw.get("stance") in ("supports", "contradicts", "supports_with_limits", "irrelevant") else "irrelevant"
    quote = str(raw.get("quote") or "").strip()[:500]
    ok_quote = quote_in(quote, body)
    out = {"stance": st, "quote": quote if ok_quote else "", "explanation": str(raw.get("explanation") or "")[:400],
           "primary_llm": raw.get("primary") is True, "same_scope": raw.get("same_scope") is not False,
           "event_date": str(raw.get("event_date") or "")[:10]}
    if st != "irrelevant" and not ok_quote:
        # el modelo no pudo mostrar en qué parte del texto lo dice: no cuenta como evidencia
        out.update(stance="unquoted", counted=False, bad_quote=quote)
    else:
        out["counted"] = st != "irrelevant"
    return out


# ---------- 6. veredicto ----------

VERDICTS = ("supported", "likely_supported", "mixed", "inconclusive", "likely_false", "false", "unverifiable")


def verdict(claim: dict, sources: list[dict], p: dict) -> dict:
    """Reglas fijas sobre las líneas de evidencia independientes (grupos). Una fuente cuenta si se leyó
    entera, su cita está en el texto, no es solo una pista (redes sociales con "lead_only"), habla del mismo
    alcance y no es más vieja que lo que pide la frescura."""
    if not claim.get("checkable", True):
        return {"verdict": "unverifiable", "lines": {"support": 0, "contra": 0, "limits": 0}, "primary": {}}
    support, contra, limits = set(), set(), set()
    prim_s = prim_c = False
    for s in sources:
        st = s.get("stance")
        if not s.get("counted") or s.get("pref") == "lead_only" or s.get("stale") or s.get("same_scope") is False:
            continue
        g = s.get("group")
        if st == "supports":
            support.add(g)
            prim_s = prim_s or s.get("primary", False)
        elif st == "supports_with_limits":
            limits.add(g)
        elif st == "contradicts":
            contra.add(g)
            prim_c = prim_c or s.get("primary", False)
    limits -= support
    S, C, L = len(support), len(contra), len(limits)
    need = max(1, int(p["min_sources"]) + (1 if claim.get("risk") == "high" and p["always_high_risk"] else 0))
    need_primary = p["require_primary"] or (claim.get("risk") == "high" and p["always_high_risk"])
    if S + L and C:
        if prim_c and not prim_s and C >= need and S + L <= 1:
            v = "likely_false"
        elif prim_s and not prim_c and S >= need and C <= 1:
            v = "likely_supported"
        else:
            v = "inconclusive"  # la evidencia está en conflicto: se muestra, no se promedia
    elif S:
        if S >= need and (prim_s or not need_primary) and (S >= 2 or prim_s):
            v = "supported"  # nunca "respaldada" con una sola fuente secundaria
        else:
            v = "likely_supported"
    elif L:
        v = "mixed"
    elif C:
        v = "false" if C >= need and prim_c else "likely_false"
    else:
        v = "inconclusive"
    return {"verdict": v, "lines": {"support": S, "contra": C, "limits": L}, "primary": {"support": prim_s, "contra": prim_c},
            "need": need, "need_primary": need_primary}


EXPLAIN_PROMPT = """Explicá en 2 o 3 oraciones simples, en {lang}, por qué la afirmación quedó como «{verdict}».
No cambies el veredicto ni agregues datos: usá solo estas evidencias.
AFIRMACIÓN: {claim}
EVIDENCIAS:
{ev}
Respondé solo con la explicación."""
V_TEXT = {"es": {"supported": "respaldada", "likely_supported": "probablemente respaldada", "mixed": "mixta o parcialmente cierta",
                 "inconclusive": "no concluyente", "likely_false": "probablemente falsa", "false": "falsa o refutada",
                 "unverifiable": "no verificable"},
          "en": {"supported": "supported", "likely_supported": "likely supported", "mixed": "mixed or partly true",
                 "inconclusive": "inconclusive", "likely_false": "likely false", "false": "false or refuted",
                 "unverifiable": "not verifiable"}}


def _explain(claim: dict, v: str, sources: list[dict], lang: str) -> str:
    ev = [f"- [{s['stance']}] {s.get('sitename') or s.get('path')}: {s.get('explanation') or s.get('quote')}"
          for s in sources if s.get("counted")][:8]
    out = generate(EXPLAIN_PROMPT.format(lang="español" if lang == "es" else "English", verdict=V_TEXT[lang][v],
                                         claim=claim["text"], ev="\n".join(ev) or "(ninguna)")) if ev else None
    return (out or "").strip()[:800]


# ---------- el proceso completo ----------

def _limitations(claim: dict, sources: list[dict], v: dict, went_web: bool, p: dict, lang: str) -> list[str]:
    es = lang == "es"
    out = []
    if any(not s.get("inspected") for s in sources if s["origin"] == "web"):
        out.append("Algunas páginas no se pudieron abrir: quedaron como evidencia no inspeccionada y no cuentan."
                   if es else "Some pages couldn't be opened: they're marked as not fully inspected and don't count.")
    if any(s.get("stance") == "unquoted" for s in sources):
        out.append("En algunas fuentes el modelo no pudo citar la frase exacta: no se contaron."
                   if es else "For some sources the model couldn't quote the exact sentence: they weren't counted.")
    if any(s.get("stale") for s in sources):
        out.append("Hay fuentes más viejas que la actualidad pedida: se muestran como contexto, no como confirmación."
                   if es else "Some sources are older than the freshness you asked for: shown as context, not confirmation.")
    if any(s.get("republished") for s in sources):
        out.append("Algunas páginas repiten el texto de otra: cuentan como una sola línea de evidencia."
                   if es else "Some pages repeat another page's text: they count as one line of evidence.")
    if v.get("need_primary") and not (v.get("primary") or {}).get("support") and v["verdict"] in ("likely_supported", "supported"):
        out.append("No apareció una fuente primaria (documento original, dato oficial o paper)."
                   if es else "No primary source (original document, official data or paper) turned up.")
    if v["verdict"] == "inconclusive" and v["lines"]["support"] and v["lines"]["contra"]:
        out.append("La evidencia está en conflicto: revisá si hablan del mismo período, lugar y definición."
                   if es else "The evidence conflicts: check whether they cover the same period, place and definition.")
    if not went_web and claim.get("checkable", True):
        out.append("Solo se usó lo guardado en brain." if es else "Only what's saved in brain was used.")
    if any(s.get("stance") == "no_model" for s in sources):
        out.append("Sin un modelo de chat en Ollama no se pudo comparar la evidencia." if es
                   else "Without an Ollama chat model the evidence couldn't be compared.")
    return out


def run(text: str, lang: str = "es", allow_web: bool | None = None, overrides: dict | None = None):
    """Generador de eventos (NDJSON en el dashboard): start · step · claim · done · error.
    El último evento `done` trae el informe completo (y queda guardado)."""
    text = (text or "").strip()[:MAX_TEXT]
    lang = "en" if lang == "en" else "es"
    if not text:
        yield {"type": "error", "code": "empty"}
        return
    p = prefs()
    if overrides:
        p = _clean(overrides, p)
        p["brave_key"] = prefs()["brave_key"]
    web_ok = p["enabled"] if allow_web is None else (allow_web and p["enabled"])
    rid = time.strftime("fc%Y%m%d-%H%M%S-") + secrets.token_hex(2)
    report = {"id": rid, "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "input": text,
              "lang": lang, "provider": p["provider"], "settings": {k: p[k] for k in ("level", "min_sources", "max_sources",
              "require_primary", "counterevidence", "freshness")}, "claims": [], "went_web": False, "notes": []}
    yield {"type": "start", "id": rid}
    yield {"type": "step", "step": "claims"}
    claims = extract_claims(text)
    if not any(c.get("llm") for c in claims):
        report["notes"].append("no_model")
    for i, claim in enumerate(claims):
        c = {**claim, "i": i, "sources": [], "decision": "", "reason": []}
        if claim["type"] not in p["claim_types"] and not (claim.get("risk") == "high" and p["always_high_risk"]):
            c.update(verdict="skipped", explanation="")
            report["claims"].append(c)
            yield {"type": "claim", "claim": c}
            continue
        if not claim.get("checkable", True):
            c.update(verdict="unverifiable", explanation=claim.get("why_not", ""))
            report["claims"].append(c)
            yield {"type": "claim", "claim": c}
            continue
        # 2. primero brain
        yield {"type": "step", "step": "local", "i": i}
        local, note = local_evidence(claim)
        if note:
            c["local_note"] = note
        age_limit = max_age(p, claim["type"])
        for s in local:
            age = _age_days(s.get("date"))
            s["stale"] = age is not None and age_limit is not None and age > age_limit
        for s in local:
            s.update(stance(claim, s))
            s["primary"] = s["type"] in ("official", "academic") or s.get("primary_llm", False)
        group_sources(local)
        v_local = verdict(claim, local, p)
        relevant = [s for s in local if s.get("counted")]
        # 3. ¿hace falta salir?
        reason = []
        if not relevant:
            reason.append("no_local")
        if any(s.get("stale") for s in local if s.get("stance") not in ("irrelevant",)) and not [s for s in relevant if not s.get("stale")]:
            reason.append("stale")
        if v_local["lines"]["support"] and v_local["lines"]["contra"]:
            reason.append("conflict")
        if v_local["verdict"] not in ("supported", "false"):
            reason.append("insufficient")
        if (p["require_primary"] or claim.get("risk") == "high") and not any(s.get("primary") and s.get("counted") for s in local):
            reason.append("no_primary")
        if claim["type"] == "news":
            reason.append("current")
        c["reason"] = sorted(set(reason))
        sources = list(local)
        if reason and web_ok:
            c["decision"] = "web"
            report["went_web"] = True
            yield {"type": "step", "step": "search", "i": i, "reason": c["reason"]}
            results, errors = [], []
            for fam, q in queries(claim, p):
                try:
                    results += [(fam, r) for r in search_web(q, p, age_limit if p["freshness"] != "any" else None)[:8]]
                except Exception as e:  # un buscador caído o con otro formato no corta la verificación
                    errors.append(e.detail if isinstance(e, FactCheckError) else f"{type(e).__name__}: {e}")
            if errors and not results:
                c["search_error"] = errors[0]
            cands = pick_candidates(results, p, p["max_sources"])
            yield {"type": "step", "step": "read", "i": i, "n": len(cands)}
            for cand in cands:
                s = fetch(cand)
                age = _age_days(s.get("date"))
                s["stale"] = (age is not None and age_limit is not None and age > age_limit) or _too_new(p, s.get("date"))
                s.update(stance(claim, s))
                s["primary"] = s["type"] in ("official", "academic") or s.get("primary_llm", False)
                sources.append(s)
                yield {"type": "step", "step": "source", "i": i, "url": s["url"], "stance": s["stance"]}
        else:
            c["decision"] = "local" if not reason else "local_only"
        group_sources(sources)
        yield {"type": "step", "step": "verdict", "i": i}
        v = verdict(claim, sources, p)
        c.update(verdict=v["verdict"], lines=v["lines"], primary=v["primary"])
        c["explanation"] = _explain(claim, v["verdict"], sources, lang)
        c["limitations"] = _limitations(claim, sources, v, c["decision"] == "web", p, lang)
        for s in sources:
            s.pop("text", None)  # el informe guarda la cita y los datos de la fuente, no la página entera
        c["sources"] = sources
        report["claims"].append(c)
        yield {"type": "claim", "claim": c}
    if any(c.get("risk") == "high" for c in report["claims"]):
        report["notes"].append("high_risk")
    if not web_ok and any(c.get("reason") for c in report["claims"]):
        report["notes"].append("web_off")
    _save_report(report)
    yield {"type": "done", "report": report}


def run_sync(text: str, lang: str = "es", allow_web: bool | None = None) -> dict:
    rep = None
    for ev in run(text, lang, allow_web):
        if ev["type"] == "done":
            rep = ev["report"]
        elif ev["type"] == "error":
            raise FactCheckError(ev["code"])
    return rep


def to_markdown(r: dict) -> str:
    """El informe para copiar (y lo que devuelve la tool fact_check)."""
    lang = r.get("lang", "es")
    es = lang == "es"
    vt = V_TEXT[lang]
    st = {"supports": "apoya" if es else "supports", "contradicts": "contradice" if es else "contradicts",
          "supports_with_limits": "apoya con límites" if es else "supports with limits",
          "irrelevant": "no responde" if es else "doesn't answer", "unquoted": "sin cita verificable" if es else "no checkable quote",
          "not_inspected": "no inspeccionada" if es else "not inspected", "no_model": "sin modelo" if es else "no model"}
    lines = [f"# Fact check · {r['created_at'][:10]}", "", f"> {r['input'][:600]}", ""]
    if r.get("went_web"):
        lines += ["*" + ("No alcanzó con tu memoria local: se consultaron fuentes externas." if es
                         else "Your local memory wasn't enough: external sources were consulted.") + "*", ""]
    if "high_risk" in r.get("notes", []):
        lines += ["**" + ("Tema de salud, derecho, finanzas o seguridad: esta verificación es informativa y no reemplaza a un profesional."
                          if es else "Health, law, finance or safety topic: this check is informational and doesn't replace a professional.") + "**", ""]
    for c in r["claims"]:
        verdict_s = vt.get(c.get("verdict"), "omitida" if es else "skipped")
        lines += [f"## {c['text']}", f"**{'Veredicto' if es else 'Verdict'}:** {verdict_s}"]
        if c.get("lines"):
            lines.append(("Líneas de evidencia independientes" if es else "Independent lines of evidence")
                         + f": +{c['lines']['support']} / −{c['lines']['contra']} / ~{c['lines'].get('limits', 0)}")
        if c.get("explanation"):
            lines += ["", c["explanation"]]
        if c.get("subclaims"):
            lines += ["", ("Partes" if es else "Parts") + ": " + "; ".join(c["subclaims"])]
        srcs = [s for s in c.get("sources", []) if s.get("stance") not in ("irrelevant",)]
        if srcs:
            lines += ["", "### " + ("Fuentes" if es else "Sources")]
            for s in srcs:
                where = s.get("url") or s.get("path")
                meta = " · ".join(x for x in [s.get("type"), s.get("sitename"), s.get("author"), s.get("date"),
                                              "brain" if s["origin"] == "brain" else "web"] if x)
                lines.append(f"- [{st.get(s.get('stance'), s.get('stance'))}] {s.get('title') or where} — {where} ({meta})")
                if s.get("quote"):
                    lines.append(f"  > {s['quote'][:300]}")
        for lim in c.get("limitations", []):
            lines.append(f"- {lim}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"
