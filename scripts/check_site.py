"""Chequeos de la landing, las docs y el changelog (docs/), para el CI en cada commit a main.

- Cada .html parsea sin etiquetas mal cerradas en los bloques principales.
- Los links y recursos relativos apuntan a archivos que existen.
- La versión de VERSION es la primera del changelog y la que dice SECURITY.md.
- Cada página de las docs tiene su bloque en inglés y en español.
Sale con código 1 si algo falla, listando todo lo que encontró.
"""
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr", "path",
        "circle", "rect", "line", "polyline", "polygon", "ellipse", "use", "stop"}
CHECKED = {"html", "head", "body", "main", "section", "article", "header", "footer", "nav", "div", "ul", "ol", "table", "details"}


class Checker(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack, self.refs, self.errors = [], [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for k in ("href", "src"):
            if a.get(k):
                self.refs.append(a[k])
        if tag in CHECKED:
            self.stack.append((tag, self.getpos()[0]))

    def handle_endtag(self, tag):
        if tag not in CHECKED:
            return
        if not self.stack or self.stack[-1][0] != tag:
            self.errors.append(f"línea {self.getpos()[0]}: </{tag}> sin abrir (abierto: {self.stack[-1] if self.stack else '-'})")
            return
        self.stack.pop()


def main() -> int:
    errors: list[str] = []
    pages = sorted(DOCS.rglob("*.html"))
    for page in pages:
        c = Checker()
        text = page.read_text(encoding="utf-8")
        # el JS de las páginas arma HTML en strings: se saca antes de parsear la estructura
        c.feed(re.sub(r"<script\b.*?</script>", "", text, flags=re.S))
        rel = page.relative_to(ROOT)
        errors += [f"{rel}: {e}" for e in c.errors]
        errors += [f"{rel}: <{t}> de la línea {n} sin cerrar" for t, n in c.stack]
        for ref in c.refs:
            if re.match(r"^(https?:|mailto:|#|data:|javascript:)", ref) or "${" in ref:
                continue
            target = (page.parent / ref.split("#")[0].split("?")[0])
            if ref.endswith("/") or target.is_dir():
                target = target / "index.html"
            if not target.exists():
                errors.append(f"{rel}: link roto → {ref}")

    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    changelog = (DOCS / "changelog" / "index.html").read_text(encoding="utf-8")
    first = re.search(r'<article class="release" id="([^"]+)"', changelog)
    if not first or first.group(1) != version:
        errors.append(f"changelog: la primera versión es {first.group(1) if first else '?'} y VERSION dice {version}")
    if f"`{version}`" not in (ROOT / "SECURITY.md").read_text(encoding="utf-8"):
        errors.append(f"SECURITY.md no menciona la versión actual ({version})")

    docs = (DOCS / "docs" / "index.html").read_text(encoding="utf-8")
    for m in re.finditer(r'<section class="page" id="([^"]+)"(.*?)</section>', docs, flags=re.S):
        if 'class="l-en"' not in m.group(2) or 'class="l-es"' not in m.group(2):
            errors.append(f"docs: la página #{m.group(1)} no tiene inglés y español")

    for e in errors:
        print("✗", e)
    print(f"{len(pages)} páginas revisadas, versión {version}: " + ("OK" if not errors else f"{len(errors)} problemas"))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
