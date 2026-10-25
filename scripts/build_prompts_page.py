#!/usr/bin/env python3
"""Genera la página «Prompts» de docs/docs/index.html desde brain_mcp/prompts.json (la misma fuente que usa la vista
Prompts del dashboard). Correrlo después de cambiar prompts.json; check_site.py falla si quedaron distintos."""
import html
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs" / "docs" / "index.html"
DATA = ROOT / "brain_mcp" / "prompts.json"

T = {
    "en": {
        "h1": "Prompts for your agent",
        "lead": "Ready-to-paste instructions so any AI uses brain well: what to read, what to cite and what not to touch. They are also in the dashboard, in the <b>Prompts</b> view, with a copy button in English and Spanish.",
        "how": "How to use them",
        "steps": ["Connect your agent (<a href=\"#agents\">Connecting agents</a>): cloud agents by URL, the ones on your computer with the one-click button.",
                  "Paste <b>Project instructions</b> into the agent's project instructions (in Cursor or Claude Code, the rules file).",
                  "Replace <code>&lt;project&gt;</code> with the name of your folder in <code>projects/</code>.",
                  "Try an everyday prompt and check in the agent which tool it called: each prompt says which one it should use.",
                  "To have the agent write its own rules, paste <b>Let the agent configure itself</b> as the first message."],
        "note": "Without brain tools (an AI that isn't connected), use <b>When the AI has no brain tools</b> and paste the note's content. If you ask an agent directly to write <code>BRAIN.md</code>, <code>FORMAT.md</code> or <code>FACT-CHECK.md</code> it may try, and brain stops it: only you edit them, from the graph.",
        "tool": "Tool", "tested": "Tested in", "untested": "Not tried in a client yet", "expect": "Expected",
        "tested_h": "What was tested",
        "tested_p": "On 2026-10-25, with brain running on an Ubuntu VM and Mistral connected by URL (the project instructions, the topic kickoff and the everyday prompts), and Manus (the English instructions, the pasted-content prompt and <i>Let the agent configure itself</i>). The central-file guard held: when a request was direct (“do it”), the agent tried to write and brain refused. <code>fact_check</code> was run as a direct tool call: it returns the sources, but without a chat model in Ollama the verdict is “inconclusive”. Saving a page needs the <code>nomic-embed-text</code> model in Ollama. Still to try: HuggingChat, and what happens when a tool fails.",
    },
    "es": {
        "h1": "Prompts para tu agente",
        "lead": "Instrucciones listas para pegar, para que cualquier IA use brain bien: qué leer, qué citar y qué no tocar. También están en el dashboard, en la vista <b>Prompts</b>, con botón para copiarlos en español e inglés.",
        "how": "Cómo usarlos",
        "steps": ["Conectá tu agente (<a href=\"#agents\">Conectar agentes</a>): los de la nube por URL, los de tu compu con el botón de un clic.",
                  "Pegá <b>Instrucciones de proyecto</b> en las instrucciones del proyecto del agente (en Cursor o Claude Code, el archivo de reglas).",
                  "Reemplazá <code>&lt;proyecto&gt;</code> por el nombre de tu carpeta en <code>projects/</code>.",
                  "Probá un prompt de uso diario y mirá en el agente qué tool llamó: cada prompt dice cuál debería usar.",
                  "Para que el agente arme sus propias reglas, pegá <b>Que el agente se configure solo</b> como primer mensaje."],
        "note": "Sin tools de brain (una IA sin conectar), usá <b>Cuando la IA no tiene tools de brain</b> y pegale el contenido de la nota. Si le pedís directo a un agente escribir <code>BRAIN.md</code>, <code>FORMAT.md</code> o <code>FACT-CHECK.md</code>, puede intentarlo y brain lo frena: los editás solo vos, desde el grafo.",
        "tool": "Tool", "tested": "Probado en", "untested": "Sin probar en un cliente", "expect": "Se espera",
        "tested_h": "Qué se probó",
        "tested_p": "El 2026-10-25, con brain corriendo en una VM Ubuntu y Mistral conectado por URL (instrucciones de proyecto, arranque de tema y los prompts de uso diario), y con Manus (las instrucciones en inglés, el prompt con contenido pegado y <i>Que el agente se configure solo</i>). La guarda de los archivos centrales funcionó: ante un pedido directo («hacelo») el agente intentó escribir y brain lo rechazó. <code>fact_check</code> se corrió como llamada directa a la tool: trae las fuentes, pero sin un modelo de chat en Ollama el veredicto sale «no concluyente». Guardar una página necesita el modelo <code>nomic-embed-text</code> en Ollama. Falta probar HuggingChat y qué pasa cuando una tool falla.",
    },
}


def block(lang, data):
    t = T[lang]
    e = lambda s: html.escape(s, quote=False)
    out = [f'<div class="l-{lang}">', f"  <h1>{t['h1']}</h1>", f'  <p class="lead">{t["lead"]}</p>', f"  <h2>{t['how']}</h2>", "  <ol>"]
    out += [f"    <li>{s}</li>" for s in t["steps"]]
    out += ["  </ol>", f"  <p>{t['note']}</p>"]
    for g in data["groups"]:
        out += [f"  <h2>{e(g['title'][lang])}</h2>", f"  <p>{e(g['intro'][lang])}</p>"]
        for it in g["items"]:
            tested = f"{t['tested']}: {e(', '.join(it['tested']))}" if it["tested"] else t["untested"]
            out += [f"  <h3>{e(it['title'][lang])}</h3>", f"  <p>{t['tool']}: <code>{e(it['tools'])}</code> · {tested}.</p>"]
            if it["expect"]:
                out.append(f"  <p>{t['expect']}: {e(it['expect'][lang])}</p>")
            out.append(f"  <pre>{e(it['text'][lang])}</pre>")
    out += [f"  <h2>{t['tested_h']}</h2>", f"  <p>{t['tested_p']}</p>", "</div>"]
    return "\n".join(out)


def page() -> str:
    data = json.loads(DATA.read_text("utf-8"))
    return ('<section class="page" id="prompts" data-group-en="Using brain" data-group-es="Usar brain" '
            'data-title-en="Prompts for agents" data-title-es="Prompts para agentes">\n'
            + block("en", data) + "\n" + block("es", data) + "\n</section>\n")


PAT = re.compile(r'<section class="page" id="prompts".*?</section>\n', re.S)


def main(check: bool = False) -> int:
    src = DOCS.read_text("utf-8")
    new = page()
    if PAT.search(src):
        out = PAT.sub(lambda m: new, src, 1)
    else:
        marker = '<section class="page" id="factcheck"'
        out = src.replace(marker, new + "\n" + marker, 1)
    if check:
        return 0 if out == src else 1
    DOCS.write_text(out, "utf-8")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main("--check" in sys.argv))
