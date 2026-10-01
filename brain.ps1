# Comando principal de brain en Windows (el equivalente de brain.sh). `brain` (brain.cmd, instalado
# por install.ps1) llama a este script.
$ErrorActionPreference = "Stop"
$Dir = $PSScriptRoot
Set-Location $Dir

$Uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $Uv) { $Uv = Join-Path $env:USERPROFILE ".local\bin\uv.exe" }
if (-not (Test-Path $Uv)) { Write-Host "No encontré uv. Instalalo con: irm https://astral.sh/uv/install.ps1 | iex"; exit 1 }

$cmd = if ($args.Count) { $args[0] } else { "" }
switch ($cmd) {
  "update" {
    if (-not (Test-Path (Join-Path $Dir ".git"))) { Write-Host "Esta carpeta no es un clon de git; no se puede actualizar sola."; exit 1 }
    Write-Host "Actualizando brain…"
    git -C $Dir pull --ff-only
    & $Uv sync --quiet
    Write-Host "Listo. Si el dashboard o Claude estaban abiertos, reinicialos para usar la versión nueva."
  }
  "uninstall" {
    $shim = Join-Path $env:USERPROFILE ".local\bin\brain.cmd"
    if ((Test-Path $shim) -and (Select-String -Quiet -Path $shim -Pattern "generado por install.ps1")) { Remove-Item $shim; Write-Host "Saqué el comando brain ($shim)." }
    else { Write-Host "No encontré el comando brain instalado." }
    Write-Host "La app y TUS DATOS (vault\ y data\) siguen en: $Dir"
    Write-Host "Si querés borrar todo: Remove-Item -Recurse -Force `"$Dir`""
    Write-Host "Acordate de desconectar los agentes antes (dashboard -> Conectar agente), o sacá 'brain' de su config."
  }
  "path" { Write-Host $Dir }
  { $_ -in "help", "-h", "--help" } {
    @"
brain: tu base de conocimiento local para Claude, ChatGPT y otros agentes.

  brain                 abre el dashboard (http://127.0.0.1:8765) y prende Ollama y Chroma
  brain --port 8766     dashboard en otro puerto
  brain --no-autostart  no prender Ollama/Chroma solos
  brain --no-browser    no abrir el navegador
  brain update          actualiza a la última versión
  brain path            muestra dónde está instalado (ahí viven vault\ y data\)
  brain uninstall       saca el comando (no borra tus datos)
  brain help            esta ayuda
"@
  }
  default {
    # "Actualizar y reiniciar" del dashboard: sale con el código 75, acá se actualiza y se vuelve a abrir
    $env:BRAIN_LAUNCHER = "1"
    $extra = @()
    while ($true) {
      & $Uv run python dashboard.py @args @extra
      if ($LASTEXITCODE -ne 75) { exit $LASTEXITCODE }
      Write-Host "Actualizando brain…"
      Remove-Item Env:BRAIN_UPDATE_FAILED -ErrorAction SilentlyContinue
      git -C $Dir pull --ff-only
      if ($LASTEXITCODE -ne 0) {
        Write-Host "No se pudo actualizar; vuelvo a abrir la versión instalada."; $env:BRAIN_UPDATE_FAILED = "pull"
      } else {
        & $Uv sync --quiet
        if ($LASTEXITCODE -ne 0) { Write-Host "Se bajó la versión nueva pero no se pudieron instalar las dependencias. Corré: brain update"; $env:BRAIN_UPDATE_FAILED = "sync" }
      }
      $extra = @("--no-browser")
    }
  }
}
