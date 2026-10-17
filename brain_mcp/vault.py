"""Operaciones sobre archivos dentro de vault/. Cada escritura queda registrada en el historial
(brain_mcp/history.py), así cualquier archivo se puede volver a una versión anterior."""
import json
import logging
import re
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

import frontmatter
import yaml

from . import history, locks

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
VAULT = (ROOT / "vault").resolve()

# El vault no se versiona con la app: se crea desde esta plantilla la primera vez.
# Estructura base (v0.03.2): BRAIN.md es la raíz; de él salen las carpetas base, FORMAT.md, FACT-CHECK.md y
# profile.md. Los tres archivos centrales y las carpetas base no los puede tocar ninguna IA (ver agent_guard):
# los edita solo el usuario, desde el editor del grafo.
CORE_FILES = ("BRAIN.md", "FORMAT.md", "FACT-CHECK.md")
TEMPLATE_DIRS = ("projects", "skills", "memory", "knowledge/sources")
BASE_DIRS = ("projects", "skills", "memory", "knowledge", "knowledge/sources")

TEMPLATE_BRAIN = """---
type: Index
name: brain
title: Índice del brain
description: Índice central del brain — dónde está cada cosa
created: {today}
updated: {today}
---
# Brain — índice

{core}

## Notas
Agregá acá lo que quieras tener a mano. Este archivo lo editás vos (desde el grafo: botón ✎ al lado del título);
las IAs lo leen pero no lo pueden cambiar.
"""

CORE_START, CORE_END = "<!-- brain:core:start -->", "<!-- brain:core:end -->"
CORE_VERSION = "1"
CORE_BLOCK = """<!-- brain:core:start -->
<!-- brain:core v{version} · lo mantiene brain -->
Empezá por acá. De este índice sale todo el vault:

- [FORMAT.md](FORMAT.md): estructura del vault, tipos de nota, campos y cómo conectar notas. **Leelo antes de escribir.**
- [FACT-CHECK.md](FACT-CHECK.md): qué hacer cuando lo que hay en el vault no alcanza y hay que buscar en internet.
- [profile.md](profile.md): quién es el usuario (leelo primero). Lo que brain sabe de él está en `memory/` (guardá hechos nuevos con `add_memory`).
- `projects/`: un proyecto por nota o carpeta, con sus archivos y subproyectos.
- `skills/`: instrucciones reutilizables (usá `list_skills` antes de tareas complejas).
- `knowledge/sources/`: páginas guardadas con `save_url` (buscá con `search_knowledge`).

BRAIN.md, FORMAT.md, FACT-CHECK.md y las carpetas base los cambia solo el usuario.
<!-- brain:core:end -->"""


# líneas de las plantillas viejas de BRAIN.md (v0.01–v0.03.1.2) que el bloque de brain reemplaza: al migrar se
# sacan solo si están tal cual (si el usuario las cambió, quedan)
OLD_BRAIN_LINES = {
    "- Perfil del usuario: `profile.md` (leelo primero para saber con quién hablás)",
    "- Perfil del usuario: [profile.md](profile.md) (leelo primero para saber con quién hablás)",
    "- Memoria sobre el usuario: `memory/` (usá `add_memory` para guardar hechos nuevos)",
    "- Proyectos: `projects/`",
    "- Skills: `skills/` (usá `list_skills` antes de tareas complejas)",
    "- Conocimiento scrapeado: `knowledge/sources/` o `search_knowledge`",
    "## Formato de las notas",
    "Markdown con frontmatter YAML, compatible con OKF (Open Knowledge Format); el detalle está en",
    "Markdown con frontmatter YAML, compatible con OKF (Open Knowledge Format): `type` (Project, Memory, Skill,",
    "[FORMAT.md](FORMAT.md). `type` (Project, SubProject, File, Memory, Skill, Source, Profile, Index, Reference o Note), `name`, `title`, `description` (una frase), `tags`, `related`,",
    "Source, Profile, Index, Reference o Note), `name`, `title`, `description` (una frase), `tags`, `related`,",
    "y las fechas `created` y `updated`, que brain completa solo. Para linkear notas sirven `[texto](ruta.md)`",
    "(Markdown estándar, más portable) o `[[nombre]]`.",
}


class VaultError(ValueError):
    pass


FORMAT_VERSION = "3"
TEMPLATE_FORMAT = """---
type: Reference
name: format
title: Formato y estructura del vault de brain
description: Estructura del vault (qué sale de BRAIN.md), qué significa cada type, los campos del frontmatter y cómo conectar notas
managed: brain
format_version: "{version}"
tags: [brain]
created: {today}
updated: {today}
---
# Formato y estructura del vault de brain

Guía para cualquier IA que lea o escriba en este vault. La mantiene brain (`managed: brain`); el usuario la puede
editar desde el grafo (✎), y entonces brain deja de reescribirla.

## Estructura

```
BRAIN.md              Index      raíz: de acá sale todo (solo el usuario lo edita)
├── FORMAT.md         Reference  esta guía (solo el usuario)
├── FACT-CHECK.md     Reference  cómo verificar y buscar afuera del vault (solo el usuario)
├── profile.md        Profile    quién es el usuario
├── memory/           Memory     un archivo por categoría, un hecho por viñeta (add_memory)
├── projects/
│   ├── <proyecto>.md            Project (proyecto de una sola nota)
│   └── <proyecto>/
│       ├── index.md             Project (la nota principal del proyecto)
│       ├── <archivo>.md         File (documentación, apuntes, entregables)
│       └── <subproyecto>/
│           ├── index.md         SubProject
│           └── <archivo>.md     File
├── skills/           Skill      instrucciones reutilizables
└── knowledge/
    ├── sources/      Source     páginas guardadas con save_url
    └── connections/  Reference  lo que traen las conexiones (se guarda solo)
```

En el grafo: BRAIN.md en el centro; de él salen FORMAT.md, FACT-CHECK.md, profile.md y las carpetas base; cada
proyecto tiene sus carpetas y archivos ("parte de"), y los archivos y proyectos se unen por tags y links.

## Lo que no podés tocar

`BRAIN.md`, `FORMAT.md` y `FACT-CHECK.md` (no se pueden escribir, mover ni borrar con las tools) y las carpetas
base (`projects/`, `skills/`, `memory/`, `knowledge/`, `knowledge/sources/`), que no se renombran ni se mueven.
Si hace falta un cambio en esos archivos, pedíselo al usuario: los edita él desde el grafo. Todo lo demás (notas
dentro de las carpetas) se crea y edita normalmente, y cada cambio queda en el historial.

## Dónde va una nota nueva

1. ¿Es un hecho sobre el usuario? → `add_memory` (nunca un archivo suelto).
2. ¿Es de un proyecto? → dentro de `projects/<proyecto>/` (si el proyecto era una sola nota `projects/<p>.md`,
   creá la carpeta `projects/<p>/` para sus archivos). Una parte grande (módulo, app, etapa) → subcarpeta con su
   `index.md`.
3. ¿Son instrucciones para repetir una tarea? → `skills/<nombre>.md`.
4. ¿Es una página web? → `save_url` (no la copies a mano).
5. Lo demás → una nota suelta (`Note`) en la raíz o donde la agrupe el usuario.

Antes de crear, buscá si ya existe (`list_vault`, `search_knowledge`): mejor completar una nota que duplicarla.

## Tipos (`type`)

| type | Qué es | Dónde va |
|---|---|---|
| `Project` | Un proyecto: objetivo, estado, decisiones, pendientes. | `projects/<nombre>.md`, o la nota principal de `projects/<nombre>/` (`index.md`, `README.md` o la que se llama como la carpeta). |
| `SubProject` | Una parte de un proyecto con entidad propia (un módulo, una app, una etapa). | La nota principal de una subcarpeta: `projects/<proyecto>/<subproyecto>/index.md`. |
| `File` | Un archivo de un proyecto que no es el proyecto en sí: documentación, apuntes, actas, entregables, specs. | Cualquier otra nota dentro de `projects/<proyecto>/…`. |
| `Memory` | Hechos sobre el usuario, uno por viñeta, con fecha. | `memory/<categoría>.md` (con `add_memory`). |
| `Skill` | Instrucciones reutilizables para una tarea. | `skills/`. |
| `Source` | Una página web guardada, con su URL (`resource`), autor y fecha. | `knowledge/sources/` (con `save_url`). |
| `Reference` | Conocimiento de consulta: capturas de conexiones, guías como esta. | `knowledge/`, `FORMAT.md`, `FACT-CHECK.md`. |
| `Profile` | El perfil del usuario. | `profile.md`. |
| `Index` | Un índice: dónde está cada cosa. | `BRAIN.md`, `*/index.md` fuera de proyectos. |
| `Note` | Una nota suelta que no es nada de lo anterior. | Cualquier otro lugar. |

Si no ponés `type`, brain lo completa según la carpeta.

## Campos

```yaml
---
type: File
name: plan-de-lanzamiento        # identificador corto y estable (lo usan los links: no lo cambies)
title: Plan de lanzamiento       # título legible
description: Fechas, canales y responsables del lanzamiento   # una frase: la muestran list_vault y el grafo
tags: [brasa, lanzamiento]       # el primer tag de un archivo de proyecto = el nombre del proyecto
related: [brasa]                 # notas relacionadas (en notas existentes, cambialo con propose_graph_change)
---
```

`created` y `updated` (`AAAA-MM-DD`) los completa brain en cada escritura.

## Cómo conectar notas

- **Proyecto ↔ sus archivos**: la ubicación ya los une ("parte de"). Además, el proyecto y cada uno de sus archivos
  llevan el tag del proyecto (`tags: [<proyecto>]`): así quedan unidos aunque se muevan, y un archivo que sirve
  para dos proyectos lleva los dos tags.
- **Entre notas**: `[texto](ruta.md)` (Markdown estándar, más portable) o `[[nombre]]` en el cuerpo. Linkeá la
  primera mención de otra nota.
- **Por tema**: `tags` en minúsculas, sin `#`, en singular (`ia`, no `IA` ni `#ias`).
- **Relaciones fuertes**: `related: [nombre]` al crear la nota. Para agregar o sacar una relación en una nota que
  ya existe usá `propose_graph_change` (el usuario la aprueba sobre una imagen).
- Nada cuelga suelto: toda nota nueva linkea (o lleva el tag de) al menos un proyecto, una nota o un tema.
"""

FACTCHECK_VERSION = "1"
PREFS_START, PREFS_END = "<!-- brain:prefs:start -->", "<!-- brain:prefs:end -->"
TEMPLATE_FACTCHECK = """---
type: Reference
name: fact-check
title: Fact check — cuándo y cómo buscar afuera del vault
description: Qué hacer cuando lo que hay en brain no alcanza y hay que buscar en internet; reglas de fuentes, citas y veredictos (va con la vista Fact check)
managed: brain
factcheck_version: "{version}"
tags: [brain, fact-check]
created: {today}
updated: {today}
---
# Fact check — cuándo y cómo buscar afuera del vault

Esta guía es para cualquier IA conectada a brain. Va junto con la vista **Fact check** del dashboard: las
preferencias del usuario de abajo se actualizan solas cuando las cambia ahí. La mantiene brain (`managed: brain`);
el usuario la puede editar desde el grafo (✎).

## 1. Primero, brain

1. Buscá en el vault: `search_knowledge` (es híbrida: temas, nombres, IDs, fechas) y, si hace falta, `read_file`.
2. Revisá `profile.md` y `memory/` si la pregunta es sobre el usuario: eso nunca se busca afuera.
3. Si lo que encontraste alcanza, respondé con eso y decí de qué nota sale.

## 2. Cuándo salir a internet

Solo si pasa alguna de estas, y si las preferencias lo permiten (abajo):
- no hay nada en el vault sobre el tema, o lo que hay no responde la pregunta;
- lo que hay está **viejo** para el tema (noticias, precios, versiones de software, leyes, salud: días o meses;
  historia o conceptos: años);
- hay **contradicciones** entre notas;
- es un dato que el usuario va a usar para decidir algo (salud, dinero, legal, seguridad).

Avisale al usuario que vas a consultar fuentes externas. Nunca pongas datos personales del usuario (nombres,
direcciones, documentos, el contenido de sus notas privadas) en una búsqueda.

## 3. Cómo buscar

- Usá la tool `fact_check` (aplica las preferencias de abajo y deja el informe en la vista Fact check). Si no la
  tenés, hacé lo mismo a mano con `read_url`.
- Separá la pregunta en **afirmaciones verificables** (una por consulta). Una opinión o una predicción no se
  verifica: decilo.
- Hacé varias consultas: una **neutral**, una que busque **evidencia**, una que busque **contraevidencia** y una que
  busque la **fuente primaria** (el organismo, el paper, la ley, el comunicado, el repositorio).
- Leé la página entera, no solo el resultado del buscador. Si no la pudiste abrir, no cuenta como fuente.

## 4. Qué fuentes cuentan

1. **Primaria**: quien produce el dato (organismo oficial, empresa sobre su producto, paper, texto de la ley,
   documentación oficial, código fuente).
2. **Secundaria confiable**: medios con editor y fecha, enciclopedias, revisiones sistemáticas.
3. **Débil**: blogs, foros, redes, notas sin autor ni fecha. Sirven como pista, no como prueba.

- Dos páginas que se copian entre sí (mismo sitio, mismo texto, misma nota de agencia) son **una sola línea** de
  evidencia, no dos.
- Toda afirmación lleva una **cita textual** que esté en la página (al menos 4 palabras seguidas), con link y fecha.
  Si no podés mostrar dónde lo dice, no lo afirmes.
- Ojo con el alcance: un dato de otro país, otra fecha u otra versión no confirma este.

## 5. Veredicto

Usá estas palabras, con el porqué en una línea:
**respaldada** (dos líneas independientes o una primaria clara) · **probablemente respaldada** · **mixta** ·
**no concluyente** · **probablemente falsa** · **falsa** · **no verificable**.
Nunca "respaldada" con una sola fuente secundaria. Si hay contradicción, "no concluyente" salvo que una fuente
primaria la resuelva. En temas de salud, derecho, finanzas o seguridad: pedí una línea más de evidencia y aclará
que es informativo y no reemplaza a un profesional.

## 6. Cómo responder

- Primero la respuesta, después las fuentes (link, tipo, fecha y la cita), qué vino de brain y qué de internet, y
  las limitaciones (qué no pudiste verificar).
- **No guardes nada en el vault** sin que el usuario lo pida. Si una fuente vale la pena, ofrecé guardarla con
  `save_url` o que la guarde desde la vista Fact check.
- Si el usuario te corrige con un dato propio, guardalo con `add_memory` (si es sobre él) o proponé actualizar la
  nota correspondiente.

## Preferencias del usuario (vista Fact check)

{prefs}
"""



def _managed_note(rel: str, text: str, version_key: str, version: str) -> None:
    """Crea una nota que mantiene brain, o la actualiza si cambió la versión de la plantilla, salvo que el usuario
    la haya editado (ya no dice `managed: brain`)."""
    p = VAULT / rel
    if p.exists():
        try:
            meta = parse(p.read_text(encoding="utf-8"))[0]
        except Exception:
            return
        if meta.get("managed") != "brain" or str(meta.get(version_key)) == version:
            return
    try:
        write_file(rel, text, op="update" if p.exists() else "create")
    except Exception as e:  # nunca frena el arranque
        log.warning("no se pudo escribir %s: %s", rel, e)


def ensure_format_note() -> None:
    """FORMAT.md: la estructura del vault y la guía de tipos para los agentes."""
    _managed_note("FORMAT.md", TEMPLATE_FORMAT.format(today=date.today().isoformat(), version=FORMAT_VERSION),
                  "format_version", FORMAT_VERSION)


def factcheck_prefs_md(prefs: dict | None = None) -> str:
    """Resumen legible de las preferencias de Fact check, para FACT-CHECK.md (lo arma factcheck.prefs_summary)."""
    try:
        from . import factcheck
        return factcheck.prefs_summary(prefs)
    except Exception as e:
        log.info("preferencias de Fact check: %s", e)
        return "(no se pudieron leer: usá los valores recomendados)"


def _prefs_block(prefs: dict | None = None) -> str:
    return f"{PREFS_START}\n{factcheck_prefs_md(prefs)}\n{PREFS_END}"


def ensure_factcheck_note() -> None:
    """FACT-CHECK.md: qué hacer cuando el vault no alcanza y hay que buscar afuera. Sale de BRAIN.md, como FORMAT.md."""
    text = TEMPLATE_FACTCHECK.format(today=date.today().isoformat(), version=FACTCHECK_VERSION, prefs=_prefs_block())
    _managed_note("FACT-CHECK.md", text, "factcheck_version", FACTCHECK_VERSION)


def sync_factcheck_prefs(prefs: dict | None = None) -> bool:
    """Reescribe el bloque de preferencias de FACT-CHECK.md (lo llama factcheck.save_prefs). Si el usuario sacó las
    marcas, no toca nada. Devuelve si cambió algo."""
    p = VAULT / "FACT-CHECK.md"
    if not p.is_file():
        return False
    text = p.read_text(encoding="utf-8")
    i, j = text.find(PREFS_START), text.find(PREFS_END)
    if i < 0 or j < i:
        return False
    new = text[:i] + _prefs_block(prefs) + text[j + len(PREFS_END):]
    if new == text:
        return False
    write_file("FACT-CHECK.md", new, op="update", stamp_meta=False)
    return True


def _core_block() -> str:
    return CORE_BLOCK.format(version=CORE_VERSION)


def ensure_brain_note() -> None:
    """BRAIN.md: si no existe, desde la plantilla. Si existe, se asegura el bloque de brain (entre marcas) con los links
    a FORMAT.md, FACT-CHECK.md, profile.md y las carpetas base; el resto del archivo es del usuario y no se toca."""
    p = VAULT / "BRAIN.md"
    today = date.today().isoformat()
    if not p.exists():
        p.write_text(TEMPLATE_BRAIN.format(today=today, core=_core_block()), encoding="utf-8")
        return
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return
    block = _core_block()
    i, j = text.find(CORE_START), text.find(CORE_END)
    if i >= 0 and j > i:
        if text[i:j + len(CORE_END)] == block:
            return
        new = text[:i] + block + text[j + len(CORE_END):]
    else:
        # va después del primer título (o del frontmatter), así es lo primero que lee un agente
        m = _FM.match(text)
        start = m.end() if m else 0
        h = re.compile(r"^# .*$", re.M).search(text, start)
        at = h.end() + 1 if h else start
        rest = "\n".join(ln for ln in text[at:].split("\n") if ln.rstrip() not in OLD_BRAIN_LINES)
        rest = re.sub(r"\n{3,}", "\n\n", rest).strip("\n")
        new = text[:at] + "\n" + block + "\n" + ("\n" + rest + "\n" if rest else "")
    try:
        write_file("BRAIN.md", new, op="format")
    except Exception as e:
        log.warning("no se pudo actualizar BRAIN.md: %s", e)


def ensure_vault() -> None:
    """Crea vault/ con la estructura base si no existe (primer arranque o repo recién clonado) y asegura los tres
    archivos centrales: BRAIN.md, FORMAT.md y FACT-CHECK.md."""
    for d in TEMPLATE_DIRS:
        (VAULT / d).mkdir(parents=True, exist_ok=True)
    ensure_brain_note()
    ensure_format_note()
    ensure_factcheck_note()


def is_core(rel: str) -> bool:
    return rel in CORE_FILES


def agent_guard(path: str, what: str = "cambiar") -> str:
    """Lo llaman las tools de los agentes (server.py) antes de escribir, mover, borrar o restaurar. Los tres
    archivos centrales y las carpetas base son del usuario: solo se editan desde el dashboard. Devuelve el path
    relativo validado."""
    rel = _rel(_resolve(path))
    if rel in CORE_FILES:
        raise VaultError(f"{rel} es un archivo central de brain: las IAs no lo pueden {what}. Lo edita el usuario desde "
                         f"el grafo de brain (botón ✎ al lado del título). Si hace falta un cambio, pedíselo.")
    if rel in BASE_DIRS or (VAULT / rel).is_dir():
        raise VaultError(f"{rel}/ es una carpeta, no una nota. Las carpetas base de brain no se mueven ni se renombran.")
    return rel


def _resolve(path: str) -> Path:
    if not path or not path.strip():
        raise VaultError("Path vacío.")
    p = Path(path)
    if p.is_absolute():
        raise VaultError(f"Path absoluto no permitido: {path}")
    if ".." in p.parts:
        raise VaultError(f"'..' no permitido en el path: {path}")
    if p.parts and p.parts[0].startswith("."):
        raise VaultError(f"No se pueden tocar archivos ocultos del vault: {path}")
    resolved = (VAULT / p).resolve()  # resuelve symlinks
    if resolved != VAULT and VAULT not in resolved.parents:
        raise VaultError(f"El path se sale del vault: {path}")
    return resolved


def _rel(p: Path) -> str:
    return p.relative_to(VAULT).as_posix()


@contextmanager
def _lock():
    """Lock entre procesos: varios server.py (Claude Code + Desktop) + el dashboard pueden escribir
    a la vez, y leer-modificar-escribir + registrar en el historial tiene que ser atómico."""
    with locks.locked(history.DATA / ".vault.lock"):
        yield


def _read_or_none(p: Path) -> str | None:
    return p.read_text(encoding="utf-8") if p.is_file() else None


# ---------- frontmatter ----------
# Los agentes (y sobre todo los modelos chicos del chat) escriben YAML a mano y es fácil que dejen un
# valor sin comillas con ": " adentro. Ese archivo después no se puede leer: el grafo y list_vault
# pierden su description sin avisar. Por eso toda escritura valida el frontmatter, lo repara si puede
# (poniendo entre comillas los valores problemáticos) y si no, rechaza la escritura con un error claro.

_FM = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)
_FM_LINE = re.compile(r"^(\s*[\w\-]+):[ \t]+(.+?)\s*$")


def _fm_ok(fm: str) -> bool:
    try:
        data = yaml.safe_load(fm)
    except yaml.YAMLError:
        return False
    return data is None or isinstance(data, dict)


def _fm_repair(fm: str) -> str:
    out = []
    for line in fm.splitlines():
        m = _FM_LINE.match(line)
        if m and not m.group(2).startswith(("'", '"', "[", "{", "|", ">", "&", "*", "!")):
            test = f"{m.group(1).strip()}: {m.group(2)}"
            if not _fm_ok(test) or not isinstance((yaml.safe_load(test) or {}).get(m.group(1).strip()), (str, int, float, bool, type(None))):
                line = f"{m.group(1)}: {json.dumps(m.group(2), ensure_ascii=False)}"
        out.append(line)
    return "\n".join(out)


def check_frontmatter(text: str, path: str = "") -> str:
    """Devuelve el texto con el frontmatter válido (reparado si hacía falta) o levanta VaultError."""
    m = _FM.match(text)
    if not m or _fm_ok(m.group(1)):
        return text
    fixed = _fm_repair(m.group(1))
    if _fm_ok(fixed):
        if path:
            log.info("frontmatter reparado en %s", path)
        return text[:m.start(1)] + fixed + text[m.end(1):]
    raise VaultError(
        f"El frontmatter YAML de {path or 'el archivo'} no es válido y no se pudo reparar. "
        "Poné entre comillas los valores que tengan ':' o '#', o usá set_frontmatter(path, fields)."
    )


# ---------- metadatos de formato (perfil OKF de brain) ----------
# Cada nota lleva `type` (qué es), `created` (cuándo se creó) y `updated` (último cambio). brain los completa
# solo en cada escritura, tocando únicamente esas líneas del YAML (no reformatea el resto del frontmatter).
# OKF (Open Knowledge Format v0.2) solo exige `type`; el resto de los campos de brain son extensiones válidas.

TYPES = {"projects": "Project", "skills": "Skill", "memory": "Memory", "knowledge": "Source"}
STAMP_FIELDS = ("type", "created", "updated")


PROJECT_MAIN = ("index", "readme", "main", "overview")
PROJECT_TYPES = ("Project", "SubProject", "File")


def _slugish(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def project_type(rel: str) -> str | None:
    """Tipo de una nota dentro de projects/ según dónde está (v0.03.1.1):
    - Project: projects/<nombre>.md, o la nota principal de projects/<nombre>/ (index, readme, main, overview o
      la que se llama como la carpeta);
    - SubProject: la nota principal de una subcarpeta dentro de un proyecto (projects/<p>/<sub>/…);
    - File: cualquier otra nota dentro de un proyecto (documentación, apuntes, entregables).
    None si no está en projects/. projects/index.md es el índice de la carpeta."""
    parts = rel.split("/")
    if parts[0] != "projects" or len(parts) < 2:
        return None
    stem = parts[-1][:-3] if parts[-1].endswith(".md") else parts[-1]
    if len(parts) == 2:
        return "Index" if stem.lower() == "index" else "Project"
    folders = parts[1:-1]
    if stem.lower() in PROJECT_MAIN or _slugish(stem) == _slugish(folders[-1]):
        return "Project" if len(folders) == 1 else "SubProject"
    return "File"


def effective_type(rel: str, meta: dict) -> str:
    """El type que vale para una nota: el del frontmatter, salvo el `Project` que v0.03.1 completó solo en
    todo projects/ (también en los archivos de adentro); ahí manda la ubicación (project_type)."""
    t = str((meta or {}).get("type") or "").strip()
    rule = note_type(rel)
    if not t:
        return rule
    if t == "Project" and rule in ("SubProject", "File"):
        return rule
    return t


def note_type(rel: str) -> str:
    pt = project_type(rel)
    if pt:
        return pt
    if rel in ("FORMAT.md", "FACT-CHECK.md"):
        return "Reference"
    if rel == "BRAIN.md" or rel.endswith("/index.md") or rel == "index.md":
        return "Index"
    if rel == "profile.md":
        return "Profile"
    if rel.startswith("knowledge/") and not rel.startswith("knowledge/sources/"):
        return "Reference"
    return TYPES.get(rel.split("/")[0], "Note") if "/" in rel else "Note"


def _day(v) -> str | None:
    """Fecha YYYY-MM-DD de un valor de frontmatter o del historial (ISO con hora), o None."""
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    m = re.match(r"\s*['\"]?(\d{4}-\d{2}-\d{2})", str(v or ""))
    return m.group(1) if m else None


def _first_seen(rel: str) -> str | None:
    """Fecha del primer cambio registrado de un archivo (para `created` de notas viejas)."""
    try:
        from contextlib import closing
        with closing(history._connect()) as con:
            row = con.execute("SELECT MIN(ts) FROM changes WHERE path = ?", (rel,)).fetchone()
        return _day(row[0]) if row and row[0] else None
    except Exception:
        return None


def stamp(text: str, rel: str, before: str | None = None, touch: bool = True, today: str | None = None,
          created: str | None = None, updated: str | None = None) -> str:
    """Completa type/created/updated en el frontmatter de `text` (una nota .md):
    - type: si falta, según la carpeta (note_type);
    - created: el que ya tenga; si no, el de la versión anterior; si no, el primer cambio del historial
      (o `created`), y si es nueva, hoy;
    - updated: hoy si `touch`; si no, solo se agrega cuando falta (con `updated` o created).
    Edita solo esas líneas; si el archivo no tiene frontmatter, le agrega uno con esos tres campos."""
    today = today or date.today().isoformat()
    m = _FM.match(text)
    meta = {}
    if m:
        try:
            meta = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            return text  # check_frontmatter ya lo validó: no debería pasar
        if not isinstance(meta, dict):
            return text
    want: dict[str, str] = {}
    if not meta.get("type"):
        want["type"] = note_type(rel)
    if not _day(meta.get("created")):
        prev = None
        if before:
            pm = _FM.match(before)
            if pm:
                try:
                    prev = _day((yaml.safe_load(pm.group(1)) or {}).get("created"))
                except (yaml.YAMLError, AttributeError):
                    prev = None
        want["created"] = prev or created or (_first_seen(rel) if before is not None else None) or today
    if touch and _day(meta.get("updated")) != today:
        want["updated"] = today
    elif not touch and not _day(meta.get("updated")):
        want["updated"] = updated or want.get("created") or _day(meta.get("created")) or today
    if not want:
        return text
    if not m:
        head = "".join(f"{k}: {want[k]}\n" for k in STAMP_FIELDS if k in want)
        return f"---\n{head}---\n" + text
    lines = m.group(1).split("\n")
    for k in STAMP_FIELDS:
        if k not in want:
            continue
        rx = re.compile(rf"^{k}\s*:")
        idx = next((i for i, ln in enumerate(lines) if rx.match(ln)), None)
        if idx is not None:
            lines[idx] = f"{k}: {want[k]}"
        elif k == "type":
            lines.insert(0, f"type: {want[k]}")
        else:
            lines.append(f"{k}: {want[k]}")
    return text[:m.start(1)] + "\n".join(lines) + text[m.end(1):]


def parse(text: str) -> tuple[dict, str]:
    """(metadata, contenido) sin romperse nunca: si el YAML es inválido, lo repara o lo ignora."""
    try:
        post = frontmatter.loads(text)
        return dict(post.metadata), post.content
    except Exception:
        pass
    try:
        post = frontmatter.loads(check_frontmatter(text))
        return dict(post.metadata), post.content
    except Exception:
        m = _FM.match(text)
        return {}, text[m.end():] if m else text


def list_files(prefix: str | None = None) -> list[dict]:
    base = _resolve(prefix) if prefix else VAULT
    if not base.exists():
        return []
    files = [base] if base.is_file() else sorted(base.rglob("*.md"))
    out = []
    for f in files:
        if any(part.startswith(".") for part in f.relative_to(VAULT).parts):
            continue
        try:
            desc = parse(f.read_text(encoding="utf-8"))[0].get("description", "")
        except Exception:
            desc = ""
        out.append({"path": _rel(f), "description": desc})
    return out


def read_file(path: str) -> str:
    p = _resolve(path)
    if not p.is_file():
        raise VaultError(f"No existe: {path}")
    return p.read_text(encoding="utf-8")


def _checked(p: Path, text: str) -> str:
    return check_frontmatter(text, _rel(p)) if p.suffix == ".md" else text


def _stamped(p: Path, text: str, before: str | None, touch: bool = True) -> str:
    return stamp(text, _rel(p), before, touch) if p.suffix == ".md" else text


def write_file(path: str, content: str, op: str | None = None, stamp_meta: bool = True) -> str:
    """stamp_meta=False escribe el contenido exacto (deshacer un cambio, volver atrás): no toca type/fechas."""
    p = _resolve(path)
    content = _checked(p, content)
    with _lock():
        before = _read_or_none(p)
        if stamp_meta:
            content = _stamped(p, content, before)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        history.record(op or ("update" if before is not None else "create"), _rel(p), before, content)
    return _rel(p)


def append_file(path: str, content: str) -> str:
    p = _resolve(path)
    with _lock():
        before = _read_or_none(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        after = _stamped(p, _checked(p, (before or "") + content), before)
        p.write_text(after, encoding="utf-8")
        history.record("append", _rel(p), before, after)
    return _rel(p)


def str_replace_file(path: str, old: str, new: str) -> str:
    p = _resolve(path)
    with _lock():
        if not p.is_file():
            raise VaultError(f"No existe: {path}")
        text = p.read_text(encoding="utf-8")
        n = text.count(old) if old else 0
        if n != 1:
            raise VaultError(f"'old' tiene que aparecer exactamente 1 vez en {path}; aparece {n}.")
        after = _stamped(p, _checked(p, text.replace(old, new, 1)), text)
        p.write_text(after, encoding="utf-8")
        history.record("edit", _rel(p), text, after)
    return _rel(p)


# campos de control que no cuentan como "actualizar la nota" (no mueven `updated`)
BOOKKEEPING = {"checked_at", "refresh_error"}


def set_frontmatter(path: str, fields: dict, op: str = "edit") -> str:
    """Cambia campos del frontmatter sin tocar el contenido. Un valor None borra el campo."""
    p = _resolve(path)
    if not isinstance(fields, dict) or not fields:
        raise VaultError("fields tiene que ser un objeto con al menos un campo, ej. {\"description\": \"...\"}.")
    with _lock():
        if not p.is_file():
            raise VaultError(f"No existe: {path}")
        text = p.read_text(encoding="utf-8")
        meta, body = parse(text)
        for k, v in fields.items():
            if v is None:
                meta.pop(str(k), None)
            else:
                meta[str(k)] = v
        after = frontmatter.dumps(frontmatter.Post(body, **meta), sort_keys=False) + "\n"
        touch = not set(map(str, fields)) <= BOOKKEEPING and "updated" not in fields
        after = _stamped(p, after, text, touch)
        p.write_text(after, encoding="utf-8")
        history.record(op, _rel(p), text, after)
    return _rel(p)


def backfill_metadata() -> dict:
    """Completa type/created/updated en las notas que no los tienen (Ajustes → Formato de las notas).
    created = primer cambio del historial (o la fecha del archivo), updated = último cambio (o la fecha del
    archivo). No cambia nada más; cada nota tocada queda en el historial con la operación "format"."""
    done, skipped = [], 0
    for f in sorted(VAULT.rglob("*.md")):
        rel = _rel(f)
        if any(part.startswith(".") for part in f.relative_to(VAULT).parts):
            continue
        with _lock():
            text = f.read_text(encoding="utf-8")
            try:
                text_ok = check_frontmatter(text, rel)
            except VaultError:
                skipped += 1
                continue
            mtime = datetime.fromtimestamp(f.stat().st_mtime).date().isoformat()
            try:
                from contextlib import closing
                with closing(history._connect()) as con:
                    first, last = con.execute("SELECT MIN(ts), MAX(ts) FROM changes WHERE path = ?", (rel,)).fetchone()
            except Exception:
                first = last = None
            after = stamp(text_ok, rel, None, touch=False, created=_day(first) or mtime, updated=_day(last) or mtime)
            after = _fix_legacy_type(after, rel)
            if after == text:
                continue
            f.write_text(after, encoding="utf-8")
            history.record("format", rel, text, after)
            done.append(rel)
    return {"updated": done, "count": len(done), "skipped": skipped}


def missing_metadata() -> int:
    """Cuántas notas no tienen type, created o updated (para Ajustes)."""
    n = 0
    for f in VAULT.rglob("*.md"):
        if any(part.startswith(".") for part in f.relative_to(VAULT).parts):
            continue
        try:
            meta = parse(f.read_text(encoding="utf-8"))[0]
        except Exception:
            continue
        rel = _rel(f)
        if (not meta.get("type") or not _day(meta.get("created")) or not _day(meta.get("updated"))
                or str(meta.get("type")) != effective_type(rel, meta)):
            n += 1
    return n


def _fix_legacy_type(text: str, rel: str) -> str:
    """`type: Project` en un archivo de adentro de un proyecto (lo completó v0.03.1) → SubProject o File."""
    m = _FM.match(text)
    if not m:
        return text
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return text
    want = effective_type(rel, meta) if isinstance(meta, dict) else None
    if not want or str(meta.get("type")) == want:
        return text
    fm = re.sub(r"(?m)^type\s*:.*$", f"type: {want}", m.group(1), count=1)
    return text[:m.start(1)] + fm + text[m.end(1):]


def _sha(text: str | None) -> str:
    import hashlib
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()


def raw_file(path: str) -> dict:
    """Para el editor del dashboard (grafo → ✎): el archivo entero, con frontmatter, y su versión."""
    p = _resolve(path)
    if p.suffix != ".md" or not p.is_file():
        raise VaultError(f"No es una nota del vault: {path}")
    text = p.read_text(encoding="utf-8")
    meta = parse(text)[0]
    return {"path": _rel(p), "content": text, "sha": _sha(text), "core": _rel(p) in CORE_FILES,
            "managed": meta.get("managed") == "brain"}


_MANAGED_LINE = re.compile(r"^managed:[ \t]*['\"]?brain['\"]?[ \t]*$", re.M)


def user_save(path: str, content: str, base_sha: str | None = None) -> dict:
    """Guarda lo que el usuario editó a mano en el dashboard (cualquier .md, incluidos BRAIN.md, FORMAT.md y
    FACT-CHECK.md, que las IAs no pueden tocar). Si el archivo cambió desde que se abrió el editor, `stale`.
    En una nota que mantenía brain (`managed: brain`), editar el contenido la pasa a `managed: user`: si no,
    brain la volvería a escribir en la próxima versión de la plantilla."""
    p = _resolve(path)
    if p.suffix != ".md":
        raise VaultError("Solo se editan notas .md.")
    if not isinstance(content, str):
        raise VaultError("Falta el contenido.")
    with _lock():
        before = _read_or_none(p)
        if before is None:
            raise VaultError(f"No existe: {path}")
        if base_sha and _sha(before) != base_sha:
            raise VaultError("stale")
        if content == before:
            return {**raw_file(path), "changed": False}
        released = False
        if parse(before)[0].get("managed") == "brain":
            m = _FM.match(content)
            if m and _MANAGED_LINE.search(m.group(1)):
                fm = _MANAGED_LINE.sub("managed: user", m.group(1), count=1)
                content = content[:m.start(1)] + fm + content[m.end(1):]
                released = True
        after = _stamped(p, _checked(p, content), before)
        p.write_text(after, encoding="utf-8")
        history.record("edit", _rel(p), before, after)
    return {**raw_file(path), "changed": True, "released": released}


def delete_file(path: str) -> str:
    p = _resolve(path)
    with _lock():
        if not p.is_file():
            raise VaultError(f"No existe: {path}")
        before = p.read_text(encoding="utf-8")
        p.unlink()
        history.record("delete", _rel(p), before, None)
    return _rel(p)


def move_file(src: str, dst: str) -> str:
    """Mueve o renombra un archivo. Queda en el historial como un borrado en src y un alta en dst,
    así cualquiera de los dos se puede restaurar."""
    a, b = _resolve(src), _resolve(dst)
    if a == b:
        raise VaultError("El origen y el destino son el mismo archivo.")
    if a.suffix != b.suffix:
        raise VaultError(f"El destino tiene que tener la misma extensión ({a.suffix}).")
    with _lock():
        if not a.is_file():
            raise VaultError(f"No existe: {src}")
        if b.exists():
            raise VaultError(f"Ya existe: {dst}. Elegí otro nombre o unilos a mano.")
        text = a.read_text(encoding="utf-8")
        b.parent.mkdir(parents=True, exist_ok=True)
        b.write_text(text, encoding="utf-8")
        history.record("move", _rel(b), None, text)
        a.unlink()
        history.record("move", _rel(a), text, None)
        # carpeta vacía que quedó atrás (nunca la raíz del vault)
        parent = a.parent
        while parent != VAULT and parent.is_dir() and not any(parent.iterdir()) and _rel(parent) not in BASE_DIRS:
            parent.rmdir()
            parent = parent.parent
    return _rel(b)


def file_history(path: str, limit: int = 30) -> list[dict]:
    return history.list_changes(path=_rel(_resolve(path)), limit=limit)


def restore_version(path: str, version_id: int) -> str:
    """Deja el archivo como quedó después del cambio version_id (si ese cambio lo borró, lo borra)."""
    p = _resolve(path)
    ch = history.get_change(version_id)
    if not ch or ch["path"] != _rel(p):
        raise VaultError(f"La versión {version_id} no corresponde a {path}.")
    with _lock():
        before = _read_or_none(p)
        if ch["after"] is None:
            if before is None:
                return _rel(p)
            p.unlink()
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(ch["after"], encoding="utf-8")
        history.record("restore", _rel(p), before, ch["after"])
    return _rel(p)
