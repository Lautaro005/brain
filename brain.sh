#!/usr/bin/env bash
# Comando principal de brain. `brain` (instalado por install.sh) llama a este script.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

UV="$(command -v uv || true)"
[ -z "$UV" ] && [ -x "$HOME/.local/bin/uv" ] && UV="$HOME/.local/bin/uv"
[ -z "$UV" ] && { echo "No encontré uv. Instalalo con: curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }


# Trae el código nuevo (git pull) sin quedarse trabado (v0.03.4). Antes, cualquier cosa que impidiera el
# `pull --ff-only` (un archivo que uv o el usuario cambió, una rama que no seguía a main) hacía que "Actualizar"
# cerrara y volviera a abrir la misma versión sin decir por qué. Ahora:
#   1) pull normal;  2) si hay cambios locales en archivos del repo, se guardan (git stash) y se reintenta;
#   3) si es una instalación (~/.brain o marcada por install.sh), nadie edita ahí: se alinea con origin.
# En un clon de desarrollo no se pisa nada: se muestra el motivo. Todo queda en data/logs/update.log.
pull_code() {
  local d="$1" log="$1/data/logs/update.log" out up
  mkdir -p "$d/data/logs"
  ulog() { printf '%s [update] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$log"; }
  ulog "antes: $(git -C "$d" rev-parse --short HEAD 2>/dev/null) ($(cat "$d/VERSION" 2>/dev/null))"
  if out="$(git -C "$d" pull --ff-only 2>&1)"; then ulog "pull ok"; printf '%s\n' "$out"; return 0; fi
  ulog "pull falló: $(printf '%s' "$out" | tr '\n' ' ' | cut -c1-400)"
  if [ -n "$(git -C "$d" status --porcelain --untracked-files=no 2>/dev/null)" ]; then
    if git -C "$d" stash push --quiet -m "brain-update $(date '+%Y-%m-%d %H:%M:%S')" >/dev/null 2>&1; then
      ulog "cambios locales guardados con git stash"
      if out="$(git -C "$d" pull --ff-only 2>&1)"; then ulog "pull ok después del stash"; printf '%s\n' "$out"; return 0; fi
      ulog "pull falló otra vez: $(printf '%s' "$out" | tr '\n' ' ' | cut -c1-400)"
    fi
  fi
  if [ -f "$d/data/.brain-install" ] || [ "$d" = "$HOME/.brain" ]; then
    up="$(git -C "$d" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)"
    [ -n "$up" ] || up="origin/main"
    if git -C "$d" fetch --quiet origin "${up#origin/}" >/dev/null 2>&1 && git -C "$d" reset --hard --quiet "$up" >/dev/null 2>&1; then
      ulog "instalación alineada con $up ($(git -C "$d" rev-parse --short HEAD))"
      echo "Se alineó la instalación con $up."
      return 0
    fi
    ulog "no se pudo alinear con $up"
  fi
  printf '%s\n' "$out" >&2
  echo "No se pudo actualizar. El detalle quedó en $log" >&2
  return 1
}

case "${1:-}" in
  update)
    [ -d "$DIR/.git" ] || { echo "Esta carpeta no es un clon de git; no se puede actualizar sola."; exit 1; }
    echo "Actualizando brain…"
    pull_code "$DIR" || exit 1
    "$UV" sync --quiet
    echo "Listo: brain está en $(cat "$DIR/VERSION" 2>/dev/null)."
    # un dashboard que ya estaba abierto sigue corriendo el código viejo: si lo abrió este lanzador, se reinicia solo
    port="${BRAIN_PORT:-8765}"
    if curl -fs -m 2 "http://127.0.0.1:$port/api/version" >/dev/null 2>&1; then
      if curl -fs -m 5 -X POST -H 'X-Brain: 1' -H 'Content-Type: application/json' -d '{}' "http://127.0.0.1:$port/api/update/apply" 2>/dev/null | grep -q '"ok": *true'; then
        echo "El dashboard abierto se reinicia con la versión nueva."
      else
        echo "El dashboard que está abierto sigue con la versión anterior: cerralo y abrilo de nuevo (o usá «Reiniciar» en Ajustes)."
      fi
    fi
    echo "Si Claude u otro agente estaba abierto, reinicialo para usar la versión nueva."
    ;;
  app)
    # la app de escritorio (la instala install.sh): ícono en la barra de menú o la bandeja, avisos del sistema y notch
    if [ "$(uname -s)" = Darwin ]; then
      APP="${BRAIN_APP_TARGET:-}"  # la ruta de la app que se acaba de actualizar (brain app-update)
      [ -n "$APP" ] && [ -d "$APP" ] || APP="$HOME/Applications/brain.app"; [ -d "$APP" ] || APP="/Applications/brain.app"
      if [ -d "$APP" ]; then exec open --env BRAIN_HOME="$DIR" "$APP"; fi
    else
      APP="$HOME/.local/opt/brain/brain.AppImage"
      if [ -x "$APP" ]; then
        export BRAIN_HOME="$DIR"
        # los AppImage necesitan FUSE 2; sin él se corre desde una extracción temporal
        if ! { [ -e /dev/fuse ] && { ldconfig -p 2>/dev/null | grep -q 'libfuse\.so\.2'; }; }; then export APPIMAGE_EXTRACT_AND_RUN=1; fi
        nohup "$APP" >/dev/null 2>&1 &
        exit 0
      fi
    fi
    echo "La app de escritorio no está instalada."
    echo "Instalala (o actualizala) con: curl -fsSL https://raw.githubusercontent.com/Lautaro005/brain/main/install.sh | bash"
    echo "O bajala del release: https://github.com/Lautaro005/brain/releases/latest"
    exit 1
    ;;
  app-update)
    # lo lanza la app de escritorio cuando su versión no es la del código (la actualización de brain trae el
    # Python nuevo pero no el binario de la app): espera a que la app se cierre (pid), baja la app del último
    # release, la instala y la vuelve a abrir. Si el release todavía no la trae, vuelve a abrir la que había.
    pid="${2:-}"
    LOG="$DIR/data/logs/app.log"; mkdir -p "$DIR/data/logs"
    log() { printf '%s [app-update] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG"; }
    if [ -n "$pid" ]; then for _ in $(seq 1 80); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
    elif pkill -TERM -x brain-app 2>/dev/null; then sleep 4; fi  # a mano: cierra la app que esté abierta (apaga su dashboard)
    log "actualizando la app de escritorio"
    if BRAIN_APP_ONLY=1 BRAIN_HOME="$DIR" bash "$DIR/install.sh" >> "$LOG" 2>&1; then log "listo, abriendo la app"; else log "no se pudo actualizar la app; abro la que estaba"; fi
    exec "$0" app
    ;;
  uninstall)
    CMD="$(command -v brain || echo "$HOME/.local/bin/brain")"
    if grep -qs "generado por install.sh" "$CMD"; then rm -f "$CMD"; echo "Saqué el comando brain ($CMD)."; else echo "No encontré el comando brain instalado."; fi
    for APP in "$HOME/Applications/brain.app" "$HOME/.local/opt/brain" "$HOME/.local/share/applications/brain.desktop" "$HOME/.local/share/icons/hicolor/128x128/apps/brain.png"; do
      if [ -e "$APP" ]; then rm -rf "$APP"; echo "Saqué la app de escritorio ($APP)."; fi
    done
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
  brain app             abre la app de escritorio (barra de menú / bandeja, avisos y notch)
  brain app-update      baja la app del último release y la vuelve a abrir
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
      if ! pull_code "$DIR"; then
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
