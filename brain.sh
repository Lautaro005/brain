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
    # El dashboard corre en segundo plano y este script le pasa las señales (Ctrl+C, kill, cerrar la
    # terminal): si no, un kill a `brain` mataría solo a bash y dejaría el dashboard y lo que prendió.
    export BRAIN_LAUNCHER=1
    EXTRA=()
    while :; do
      "$UV" run python dashboard.py "$@" ${EXTRA[@]+"${EXTRA[@]}"} &
      child=$!
      trap 'kill -TERM "$child" 2>/dev/null' INT TERM HUP
      set +e
      wait "$child"; code=$?
      # wait vuelve antes si llega una señal: esperar a que el dashboard termine de apagar todo
      while kill -0 "$child" 2>/dev/null; do wait "$child"; code=$?; done
      set -e
      trap - INT TERM HUP
      [ "$code" = 75 ] || exit "$code"
      echo "Actualizando brain…"
      unset BRAIN_UPDATE_FAILED
      if ! git -C "$DIR" pull --ff-only; then
        echo "No se pudo actualizar; vuelvo a abrir la versión instalada."
        export BRAIN_UPDATE_FAILED=pull
      elif ! "$UV" sync --quiet; then
        echo "Se bajó la versión nueva pero no se pudieron instalar las dependencias. Corré: brain update"
        export BRAIN_UPDATE_FAILED=sync
      fi
      EXTRA=(--no-browser)
    done
    ;;
esac
