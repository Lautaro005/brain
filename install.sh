#!/usr/bin/env bash
# Instalador de brain. Uso:
#   curl -fsSL https://raw.githubusercontent.com/Lautaro005/brain/main/install.sh | bash
#
# Deja la app en ~/.brain (tus datos quedan ahí, en vault/ y data/) y el comando `brain` en
# ~/.local/bin. Correrlo de nuevo actualiza la instalación.
# Variables opcionales: BRAIN_HOME (carpeta), BRAIN_REPO, BRAIN_BRANCH, BRAIN_BIN.
set -euo pipefail

REPO="${BRAIN_REPO:-https://github.com/Lautaro005/brain}"
BRANCH="${BRAIN_BRANCH:-main}"
DIR="${BRAIN_HOME:-$HOME/.brain}"
BIN="${BRAIN_BIN:-$HOME/.local/bin}"

bold=$'\033[1m'; dim=$'\033[2m'; yellow=$'\033[33m'; green=$'\033[32m'; reset=$'\033[0m'
say()  { printf "%s==>%s %s\n" "$bold" "$reset" "$*"; }
warn() { printf "%s!%s %s\n" "$yellow" "$reset" "$*"; }
fail() { printf "%sx%s %s\n" "$yellow" "$reset" "$*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || fail "Por ahora brain es solo para macOS."
command -v git >/dev/null || fail "Falta git. Instalá las herramientas de línea de comandos: xcode-select --install"
command -v curl >/dev/null || fail "Falta curl."

# ---------- uv (maneja Python y dependencias) ----------
UV="$(command -v uv || true)"
[ -z "$UV" ] && [ -x "$HOME/.local/bin/uv" ] && UV="$HOME/.local/bin/uv"
if [ -z "$UV" ]; then
  say "Instalando uv…"
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
  UV="$HOME/.local/bin/uv"
  [ -x "$UV" ] || fail "No se pudo instalar uv. Probá a mano: brew install uv"
fi

# ---------- código ----------
if [ -d "$DIR/.git" ]; then
  say "Actualizando brain en $DIR"
  git -C "$DIR" pull --ff-only --quiet
elif [ -e "$DIR" ]; then
  fail "$DIR ya existe y no es una instalación de brain. Movelo o usá BRAIN_HOME=/otra/carpeta."
else
  say "Descargando brain en $DIR"
  git clone --quiet --depth 1 --branch "$BRANCH" "$REPO" "$DIR"
fi
cd "$DIR"

say "Instalando dependencias (la primera vez tarda un par de minutos)…"
"$UV" sync --quiet

say "Instalando Chromium para scrapear sitios con JavaScript…"
"$UV" run --quiet playwright install chromium >/dev/null 2>&1 || warn "No se pudo instalar Chromium; después: cd $DIR && uv run playwright install chromium"

# ---------- Ollama (embeddings) ----------
OLLAMA="$(command -v ollama || true)"
[ -z "$OLLAMA" ] && [ -x /Applications/Ollama.app/Contents/Resources/ollama ] && OLLAMA=/Applications/Ollama.app/Contents/Resources/ollama
if [ -z "$OLLAMA" ] && command -v brew >/dev/null; then
  say "Instalando Ollama con Homebrew…"
  brew install --quiet ollama >/dev/null && OLLAMA="$(command -v ollama || true)"
fi
if [ -n "$OLLAMA" ]; then
  started=""
  if ! curl -sf http://127.0.0.1:11434/api/version >/dev/null; then
    "$OLLAMA" serve >/dev/null 2>&1 &
    started=$!
    for _ in 1 2 3 4 5 6 7 8 9 10; do curl -sf http://127.0.0.1:11434/api/version >/dev/null && break; sleep 1; done
  fi
  if "$OLLAMA" list 2>/dev/null | grep -q '^nomic-embed-text'; then
    say "Modelo de embeddings ya instalado"
  else
    say "Bajando el modelo de embeddings (nomic-embed-text, ~270 MB)…"
    "$OLLAMA" pull nomic-embed-text >/dev/null 2>&1 || warn "No se pudo bajar el modelo; después: ollama pull nomic-embed-text"
  fi
  # el modelo de chat (Chat, resúmenes de fuentes y entidades del grafo) es opcional y pesa ~2 GB:
  # no se baja solo, solo se avisa al final si no hay ninguno
  chat_model=1
  "$OLLAMA" list 2>/dev/null | awk 'NR>1 {print $1}' | grep -vqi 'embed' || chat_model=0
  if [ -n "$started" ]; then kill "$started" 2>/dev/null || true; fi
else
  warn "No encontré Ollama. Bajalo de https://ollama.com/download y después corré: ollama pull nomic-embed-text"
fi

# ---------- comando `brain` ----------
mkdir -p "$BIN"
cat > "$BIN/brain" <<EOF
#!/usr/bin/env bash
# comando de brain (generado por install.sh)
exec "$DIR/brain.sh" "\$@"
EOF
chmod +x "$BIN/brain"

on_path=1
case ":$PATH:" in *":$BIN:"*) ;; *) on_path=0 ;; esac
if [ "$on_path" = 0 ]; then
  rc="$HOME/.zshrc"; [ "${SHELL##*/}" = "bash" ] && rc="$HOME/.bash_profile"
  line="export PATH=\"$BIN:\$PATH\""
  grep -qsF "$line" "$rc" || printf '\n# brain\n%s\n' "$line" >> "$rc"
fi

printf "\n%s✓ brain instalado%s en %s\n\n" "$green" "$reset" "$DIR"
if [ "$on_path" = 0 ]; then
  printf "  Abrí una terminal nueva (o corré %ssource %s%s) y escribí:\n\n" "$dim" "$rc" "$reset"
else
  printf "  Escribí:\n\n"
fi
printf "    %sbrain%s            abre el dashboard\n" "$bold" "$reset"
printf "    %sbrain update%s     actualiza a la última versión\n" "$bold" "$reset"
printf "    %sbrain help%s       todos los comandos\n\n" "$bold" "$reset"
printf "  Primer paso: en el dashboard, andá a %sConectar agente%s y conectá Claude o ChatGPT.\n\n" "$bold" "$reset"
if [ "${chat_model:-1}" = 0 ]; then
  printf "  Opcional: %sollama pull llama3.2%s (~2 GB) activa el Chat, los resúmenes de fuentes\n" "$bold" "$reset"
  printf "  y las entidades del grafo. Sin él, todo lo demás funciona igual.\n\n"
fi
