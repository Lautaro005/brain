#!/usr/bin/env bash
# Registra brain-mcp en Claude Desktop. Correr con Claude CERRADO (Cmd+Q): la app reescribe su
# config al salir y pisaría el cambio.
set -e
if pgrep -xq Claude; then echo "Claude sigue abierto. Cerralo con Cmd+Q y volvé a correr esto."; exit 1; fi
DIR="$(cd "$(dirname "$0")" && pwd)"
# path absoluto de uv: Claude Desktop no hereda el PATH del shell
UV="$(command -v uv || true)"
[ -z "$UV" ] && [ -x "$HOME/.local/bin/uv" ] && UV="$HOME/.local/bin/uv"
[ -z "$UV" ] && { echo "No encontré uv (brew install uv)."; exit 1; }
CFG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
mkdir -p "$(dirname "$CFG")"
[ -f "$CFG" ] || echo '{}' > "$CFG"
cp "$CFG" "$CFG.bak-$(date +%s)"
/usr/bin/python3 - "$CFG" "$UV" "$DIR" <<'PY'
import json, sys
p, uv, project = sys.argv[1:4]
d = json.load(open(p))
d.setdefault("mcpServers", {})["brain"] = {
    "command": uv,
    "args": ["run", "--directory", project, "python", "server.py"],
}
json.dump(d, open(p, "w"), indent=2, ensure_ascii=False)
print("OK: brain agregado a", p)
PY
open -a Claude
