#!/usr/bin/env bash
# Instalador de brain para macOS y Linux. Uso:
#   curl -fsSL https://raw.githubusercontent.com/Lautaro005/brain/main/install.sh | bash
# (En Windows: irm https://raw.githubusercontent.com/Lautaro005/brain/main/install.ps1 | iex)
#
# Deja la app en ~/.brain (tus datos quedan ahí, en vault/ y data/) y el comando `brain` en
# ~/.local/bin. Correrlo de nuevo actualiza la instalación. No pide nada por teclado.
# Instala dos cosas: brain (el comando `brain`, dashboard en el navegador) y la app de escritorio
# (ícono en la barra de menú o la bandeja, avisos del sistema y panel del notch), que se baja del
# último release de GitHub. Sin la app (o si el release todavía no la trae): `--no-app` o BRAIN_NO_APP=1.
# Variables opcionales: BRAIN_HOME (carpeta), BRAIN_REPO, BRAIN_BRANCH, BRAIN_BIN, BRAIN_NO_APP=1,
# BRAIN_SKIP_OLLAMA=1 y BRAIN_SKIP_CHROMIUM=1 (para CI o si los instalás vos). BRAIN_APP_ONLY=1 vuelve a bajar solo la app.
set -euo pipefail

for arg in "$@"; do
  case "$arg" in --no-app) BRAIN_NO_APP=1 ;; esac
done

REPO="${BRAIN_REPO:-https://github.com/Lautaro005/brain}"
BRANCH="${BRAIN_BRANCH:-main}"
DIR="${BRAIN_HOME:-$HOME/.brain}"
BIN="${BRAIN_BIN:-$HOME/.local/bin}"

bold=$'\033[1m'; dim=$'\033[2m'; yellow=$'\033[33m'; green=$'\033[32m'; reset=$'\033[0m'
say()  { printf "%s==>%s %s\n" "$bold" "$reset" "$*"; }
warn() { printf "%s!%s %s\n" "$yellow" "$reset" "$*"; }
fail() { printf "%sx%s %s\n" "$yellow" "$reset" "$*" >&2; exit 1; }

OS="$(uname -s)"
case "$OS" in
  Darwin|Linux) ;;
  MINGW*|MSYS*|CYGWIN*) fail "En Windows usá PowerShell: irm https://raw.githubusercontent.com/Lautaro005/brain/main/install.ps1 | iex" ;;
  *) fail "Sistema no soportado: $OS (brain corre en macOS, Linux y Windows)." ;;
esac
if ! command -v git >/dev/null; then
  if [ "$OS" = Darwin ]; then fail "Falta git. Instalá las herramientas de línea de comandos: xcode-select --install"
  else fail "Falta git. Instalalo con el gestor de tu distro (ej. sudo apt install git)."; fi
fi
command -v curl >/dev/null || fail "Falta curl."

# BRAIN_APP_ONLY=1 (lo usa `brain app-update`): solo se vuelve a bajar la app, sin tocar el código ni las dependencias
if [ -z "${BRAIN_APP_ONLY:-}" ]; then

# ---------- uv (maneja Python y dependencias) ----------
UV="$(command -v uv || true)"
[ -z "$UV" ] && [ -x "$HOME/.local/bin/uv" ] && UV="$HOME/.local/bin/uv"
if [ -z "$UV" ]; then
  say "Instalando uv…"
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
  UV="$HOME/.local/bin/uv"
  [ -x "$UV" ] || fail "No se pudo instalar uv. Mirá https://docs.astral.sh/uv/getting-started/installation/"
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

if [ -z "${BRAIN_SKIP_CHROMIUM:-}" ]; then
  say "Instalando Chromium para scrapear sitios con JavaScript…"
  if ! "$UV" run --quiet playwright install chromium >/dev/null 2>&1; then
    warn "No se pudo instalar Chromium; después: cd $DIR && uv run playwright install chromium"
  elif [ "$OS" = Linux ]; then
    # en Linux Chromium necesita librerías del sistema; instalarlas pide sudo, así que solo se avisa
    "$UV" run --quiet python -c "from playwright.sync_api import sync_playwright as p; b=p().start().chromium.launch(); b.close()" >/dev/null 2>&1 \
      || warn "A Chromium le faltan librerías del sistema. Instalalas con: cd $DIR && sudo \"$UV\" run playwright install-deps chromium"
  fi
fi

# ---------- Ollama (embeddings) ----------
chat_model=1
if [ -z "${BRAIN_SKIP_OLLAMA:-}" ]; then
  OLLAMA="$(command -v ollama || true)"
  [ -z "$OLLAMA" ] && [ -x /Applications/Ollama.app/Contents/Resources/ollama ] && OLLAMA=/Applications/Ollama.app/Contents/Resources/ollama
  if [ -z "$OLLAMA" ] && [ "$OS" = Darwin ] && command -v brew >/dev/null; then
    say "Instalando Ollama con Homebrew…"
    brew install --quiet ollama >/dev/null && OLLAMA="$(command -v ollama || true)"
  fi
  if [ -z "$OLLAMA" ] && [ "$OS" = Linux ] && sudo -n true 2>/dev/null; then
    # el instalador oficial necesita sudo; solo se usa si sudo no pide contraseña (este script no puede preguntar)
    say "Instalando Ollama con su instalador oficial…"
    curl -fsSL https://ollama.com/install.sh | sh >/dev/null 2>&1 && OLLAMA="$(command -v ollama || true)"
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
    "$OLLAMA" list 2>/dev/null | awk 'NR>1 {print $1}' | grep -vqi 'embed' || chat_model=0
    if [ -n "$started" ]; then kill "$started" 2>/dev/null || true; fi
  elif [ "$OS" = Linux ]; then
    warn "No encontré Ollama. Instalalo con: curl -fsSL https://ollama.com/install.sh | sh  (y después: ollama pull nomic-embed-text)"
  else
    warn "No encontré Ollama. Bajalo de https://ollama.com/download y después corré: ollama pull nomic-embed-text"
  fi
fi

fi

# ---------- app de escritorio (barra de menú / bandeja) ----------
# Best-effort: si algo falla se avisa y brain sigue funcionando en la terminal. Los datos no se tocan.
APP_REPO="${BRAIN_APP_REPO:-Lautaro005/brain}"
app_installed=0
install_app() {
  local json urls url tmp app_dir app tag
  if [ "$OS" = Linux ]; then
    case "$(uname -m)" in x86_64|amd64) ;; *) warn "No hay app de escritorio para $(uname -m) todavía; brain sigue andando en la terminal."; return 0 ;; esac
  fi
  json="$(curl -fsSL -H 'Accept: application/vnd.github+json' "https://api.github.com/repos/$APP_REPO/releases/latest")" \
    || { warn "No pude consultar el último release; sin app de escritorio (volvé a correr este instalador más tarde)."; return 0; }
  # con `set -e` y `pipefail`, un grep sin resultados (release sin archivos) mataría el instalador en silencio: de ahí los `|| true`
  tag="$(printf '%s' "$json" | grep -o '"tag_name": *"[^"]*"' | head -n1 | sed 's/.*"\([^"]*\)"$/\1/' || true)"
  urls="$(printf '%s' "$json" | grep -o '"browser_download_url": *"[^"]*"' | sed 's/.*"\(https[^"]*\)"$/\1/' || true)"
  if [ "$OS" = Darwin ]; then
    url="$(printf '%s\n' "$urls" | grep -i '\.dmg$' | head -n1 || true)"
  else
    url="$(printf '%s\n' "$urls" | grep -i 'amd64\.AppImage$' | head -n1 || true)"
  fi
  [ -n "$url" ] || { warn "El release $tag todavía no trae la app para $OS; brain sigue andando en la terminal."; return 0; }
  tmp="$(mktemp -d)"
  say "Descargando la app de escritorio ($tag)…"
  if ! curl -fL --progress-bar "$url" -o "$tmp/download"; then warn "No se pudo bajar la app."; rm -rf "$tmp"; return 0; fi
  if [ "$OS" = Darwin ]; then
    app_dir="$HOME/Applications"; app="$app_dir/brain.app"
    mkdir -p "$app_dir" "$tmp/mnt"
    if hdiutil attach -nobrowse -readonly -quiet -mountpoint "$tmp/mnt" "$tmp/download" >/dev/null 2>&1 && [ -d "$tmp/mnt/brain.app" ]; then
      rm -rf "$app"; cp -R "$tmp/mnt/brain.app" "$app"
      xattr -dr com.apple.quarantine "$app" 2>/dev/null || true
      app_installed=1
    else
      warn "No pude abrir el .dmg; instalalo a mano desde https://github.com/$APP_REPO/releases/latest"
    fi
    hdiutil detach -quiet "$tmp/mnt" >/dev/null 2>&1 || true
  else
    app_dir="$HOME/.local/opt/brain"; app="$app_dir/brain.AppImage"
    mkdir -p "$app_dir" "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/128x128/apps"
    mv "$tmp/download" "$app"; chmod +x "$app"
    cp "$DIR/apps/desktop/src-tauri/icons/128x128.png" "$HOME/.local/share/icons/hicolor/128x128/apps/brain.png" 2>/dev/null || true
    cat > "$HOME/.local/share/applications/brain.desktop" <<ENTRY
[Desktop Entry]
Type=Application
Name=brain
Comment=Tu base de conocimiento local para agentes de IA
Exec=$BIN/brain app
Icon=brain
Categories=Office;Utility;
Terminal=false
StartupWMClass=brain-app
ENTRY
    app_installed=1
  fi
  rm -rf "$tmp"
}
if [ -z "${BRAIN_NO_APP:-}" ]; then install_app; fi
if [ -n "${BRAIN_APP_ONLY:-}" ]; then exit 0; fi

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
  case "${SHELL##*/}" in
    zsh) rc="$HOME/.zshrc" ;;
    bash) if [ "$OS" = Darwin ]; then rc="$HOME/.bash_profile"; else rc="$HOME/.bashrc"; fi ;;
    *) rc="$HOME/.profile" ;;
  esac
  line="export PATH=\"$BIN:\$PATH\""
  grep -qsF "$line" "$rc" || printf '\n# brain\n%s\n' "$line" >> "$rc"
fi

printf "\n%s✓ brain instalado%s en %s\n\n" "$green" "$reset" "$DIR"
if [ "$on_path" = 0 ]; then
  printf "  Abrí una terminal nueva (o corré %ssource %s%s) y escribí:\n\n" "$dim" "$rc" "$reset"
else
  printf "  Escribí:\n\n"
fi
printf "    %sbrain%s            abre el dashboard en el navegador (versión de terminal)\n" "$bold" "$reset"
if [ "$app_installed" = 1 ]; then
  printf "    %sbrain app%s        abre la app de escritorio (queda en la barra de menú / bandeja)\n" "$bold" "$reset"
fi
printf "    %sbrain update%s     actualiza a la última versión\n" "$bold" "$reset"
printf "    %sbrain help%s       todos los comandos\n\n" "$bold" "$reset"
printf "  Primer paso: en el dashboard, andá a %sConectar agente%s y conectá Claude o ChatGPT.\n\n" "$bold" "$reset"
if [ "$chat_model" = 0 ]; then
  printf "  Opcional: %sollama pull llama3.2%s (~2 GB) activa el Chat, los resúmenes de fuentes\n" "$bold" "$reset"
  printf "  y las entidades del grafo. Sin él, todo lo demás funciona igual.\n\n"
fi
