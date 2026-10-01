#!/usr/bin/env bash
# Comando principal de brain. `brain` (instalado por install.sh) llama a este script.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

UV="$(command -v uv || true)"
[ -z "$UV" ] && [ -x "$HOME/.local/bin/uv" ] && UV="$HOME/.local/bin/uv"
[ -z "$UV" ] && { echo "No encontré uv. Instalalo con: curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }

case "${1:-}" in
  update)
    [ -d "$DIR/.git" ] || { echo "Esta carpeta no es un clon de git; no se puede actualizar sola."; exit 1; }
    echo "Actualizando brain…"
    git -C "$DIR" pull --ff-only
    "$UV" sync --quiet
    echo "Listo. Si el dashboard o Claude estaban abiertos, reinicialos para usar la versión nueva."
    ;;
  uninstall)
    CMD="$(command -v brain || echo "$HOME/.local/bin/brain")"
    if grep -qs "generado por install.sh" "$CMD"; then rm -f "$CMD"; echo "Saqué el comando brain ($CMD)."; else echo "No encontré el comando brain instalado."; fi
    echo "La app y TUS DATOS (vault/ y data/) siguen en: $DIR"
    echo "Si querés borrar todo: rm -rf \"$DIR\""
    echo "Acordate de desconectar los agentes antes (dashboard → Conectar agente), o sacá 'brain' de su config."
    ;;
  path)
    echo "$DIR"
    ;;
  help|-h|--help)
    cat <<EOF
brain: tu base de conocimiento local para Claude, ChatGPT y otros agentes.

  brain                 abre el dashboard (http://127.0.0.1:8765) y prende Ollama y Chroma
  brain --port 8766     dashboard en otro puerto
  brain --no-autostart  no prender Ollama/Chroma solos
  brain --no-browser    no abrir el navegador
  brain update          actualiza a la última versión
  brain path            muestra dónde está instalado (ahí viven vault/ y data/)
  brain uninstall       saca el comando (no borra tus datos)
  brain help            esta ayuda
EOF
    ;;
  *)
    # "Actualizar y reiniciar" del dashboard: sale con el código 75, acá se actualiza y se vuelve a abrir
    # en esta misma terminal (sin abrir otra pestaña del navegador: la que estaba se recarga sola).
    export BRAIN_LAUNCHER=1
    EXTRA=()
    while :; do
      set +e
      "$UV" run python dashboard.py "$@" ${EXTRA[@]+"${EXTRA[@]}"}
      code=$?
      set -e
      [ "$code" = 75 ] || exit "$code"
      echo "Actualizando brain…"
      if git -C "$DIR" pull --ff-only && "$UV" sync --quiet; then
        unset BRAIN_UPDATE_FAILED
      else
        echo "No se pudo actualizar; vuelvo a abrir la versión instalada."
        export BRAIN_UPDATE_FAILED=1
      fi
      EXTRA=(--no-browser)
    done
    ;;
esac
