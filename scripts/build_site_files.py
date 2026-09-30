"""Genera docs/llms.txt, docs/sitemap.xml y docs/robots.txt a partir de las páginas del sitio.

    python3 scripts/build_site_files.py          # los escribe
    python3 scripts/build_site_files.py --check  # falla si están desactualizados (lo usa check_site.py)

llms.txt (https://llmstxt.org) lista cada página de las docs con la primera línea (el `.lead` en
inglés) de esa página, así lo que ve un modelo coincide con lo que dice el sitio. Correrlo después
de agregar o cambiar páginas de las docs.
"""
import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
BASE = "https://lautaro005.github.io/brain/"
PAGES = [("", "1.0", "weekly"), ("docs/", "0.9", "weekly"), ("changelog/", "0.7", "weekly"),
         ("privacy/", "0.3", "monthly"), ("llms.txt", "0.3", "monthly")]


def _docs_pages() -> list[tuple[str, str, str, str]]:
    docs = (DOCS / "docs" / "index.html").read_text(encoding="utf-8")
    out = []
    for m in re.finditer(r'<section class="page" id="([^"]+)"[^>]*data-group-en="([^"]+)"[^>]*data-title-en="([^"]+)"[^>]*>(.*?)</section>',
                         docs, re.S):
        pid, group, title, body = m.groups()
        en = re.search(r'<div class="l-en">(.*?)</div>\s*<div class="l-es">', body, re.S)
        lead = re.search(r'<p class="lead">(.*?)</p>', en.group(1) if en else "", re.S)
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", lead.group(1)))).strip() if lead else ""
        out.append((group, pid, title, text))
    return out


def llms_txt() -> str:
    lines = [
        "# brain", "",
        "> brain is a local MCP server that gives Claude, ChatGPT, Cursor and any other AI agent the same memory about you: "
        "your notes, projects, profile, what other chatbots know about you and the web pages you saved, with hybrid "
        "(semantic + keyword) search. Everything stays on your computer (macOS, Linux or Windows). Free and "
        "source-available (MIT + Commons Clause: use and modify, don't sell).", "",
        "- Install (macOS, Linux): `curl -fsSL https://raw.githubusercontent.com/Lautaro005/brain/main/install.sh | bash`",
        "- Install (Windows, PowerShell): `irm https://raw.githubusercontent.com/Lautaro005/brain/main/install.ps1 | iex`, "
        "or `brain-setup.exe` from the latest release",
        "- Then run `brain` to open the local dashboard at http://127.0.0.1:8765 and connect your agents in one click.",
        "- Source code: https://github.com/Lautaro005/brain",
    ]
    group = None
    for g, pid, title, text in _docs_pages():
        if g != group:
            lines += ["", f"## {g}", ""]
            group = g
        lines.append(f"- [{title}]({BASE}docs/#{pid})" + (f": {text}" if text else ""))
    lines += ["", "## Optional", "",
              f"- [Changelog]({BASE}changelog/): every release with its date and what changed",
              f"- [Privacy policy]({BASE}privacy/): what the website and the app send and to whom (the website chat is provided by DokBot)",
              "- [README](https://github.com/Lautaro005/brain/blob/main/README.md): the full project overview on GitHub",
              "- [Security policy](https://github.com/Lautaro005/brain/blob/main/SECURITY.md): supported versions and how to report a vulnerability",
              ""]
    return "\n".join(lines)


def sitemap(lastmod: str) -> str:
    xml = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for path, prio, freq in PAGES:
        xml += ["  <url>", f"    <loc>{BASE}{path}</loc>", f"    <lastmod>{lastmod}</lastmod>",
                f"    <changefreq>{freq}</changefreq>", f"    <priority>{prio}</priority>", "  </url>"]
    return "\n".join(xml + ["</urlset>"]) + "\n"


def robots() -> str:
    return f"User-agent: *\nAllow: /\n\nSitemap: {BASE}sitemap.xml\n"


def _lastmod() -> str:
    """La fecha del release más nuevo del changelog (así el sitemap cambia solo cuando hay versión nueva)."""
    ch = (DOCS / "changelog" / "index.html").read_text(encoding="utf-8")
    m = re.search(r'<time class="date" datetime="([\d-]+)"', ch)
    return m.group(1) if m else "2026-01-01"


def expected() -> dict[Path, str]:
    return {DOCS / "llms.txt": llms_txt(), DOCS / "sitemap.xml": sitemap(_lastmod()), DOCS / "robots.txt": robots()}


def main() -> int:
    check = "--check" in sys.argv
    stale = []
    for path, text in expected().items():
        if check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(path.relative_to(ROOT).as_posix())
        else:
            path.write_text(text, encoding="utf-8")
    if stale:
        print("Desactualizados (corré python3 scripts/build_site_files.py): " + ", ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
