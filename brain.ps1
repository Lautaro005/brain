# Comando principal de brain en Windows (el equivalente de brain.sh). `brain` (brain.cmd, instalado
# por install.ps1) llama a este script.
$ErrorActionPreference = "Stop"
$Dir = $PSScriptRoot
Set-Location $Dir

$Uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $Uv) { $Uv = Join-Path $env:USERPROFILE ".local\bin\uv.exe" }
if (-not (Test-Path $Uv)) { Write-Host "No encontré uv. Instalalo con: irm https://astral.sh/uv/install.ps1 | iex"; exit 1 }


# Trae el código nuevo sin quedarse trabado (v0.03.4; ver pull_code en brain.sh): pull, y si falla guarda los cambios
# locales (git stash) y reintenta; en una instalación (data\.brain-install o %USERPROFILE%\.brain) se alinea con origin.
function Pull-Code($d) {
  $ErrorActionPreference = "Continue"
  $logDir = Join-Path $d "data\logs"; New-Item -ItemType Directory -Force -Path $logDir | Out-Null
  $log = Join-Path $logDir "update.log"
  function ULog($m) { Add-Content -Path $log -Value ("{0} [update] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m) -Encoding UTF8 }
  $out = git -C $d pull --ff-only 2>&1
  if ($LASTEXITCODE -eq 0) { ULog "pull ok"; $out | ForEach-Object { Write-Host $_ }; return $true }
  ULog ("pull falló: " + (($out | Out-String) -replace "\s+", " "))
  if (git -C $d status --porcelain --untracked-files=no) {
    git -C $d stash push --quiet -m ("brain-update " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss")) *> $null
    if ($LASTEXITCODE -eq 0) {
      ULog "cambios locales guardados con git stash"
      git -C $d pull --ff-only *> $null
      if ($LASTEXITCODE -eq 0) { ULog "pull ok después del stash"; return $true }
    }
  }
  $home1 = Join-Path $env:USERPROFILE ".brain"
  if ((Test-Path (Join-Path $d "data\.brain-install")) -or ($d -eq $home1)) {
    $up = (git -C $d rev-parse --abbrev-ref --symbolic-full-name "@{u}" 2>$null)
    if (-not $up) { $up = "origin/main" }
    git -C $d fetch --quiet origin ($up -replace "^origin/", "") *> $null
    if ($LASTEXITCODE -eq 0) {
      git -C $d reset --hard --quiet $up *> $null
      if ($LASTEXITCODE -eq 0) { ULog "instalación alineada con $up"; Write-Host "Se alineó la instalación con $up."; return $true }
    }
    ULog "no se pudo alinear con $up"
  }
  Write-Host ($out | Out-String)
  Write-Host "No se pudo actualizar. El detalle quedó en $log"
  return $false
}

$cmd = if ($args.Count) { $args[0] } else { "" }
switch ($cmd) {
  "update" {
    if (-not (Test-Path (Join-Path $Dir ".git"))) { Write-Host "Esta carpeta no es un clon de git; no se puede actualizar sola."; exit 1 }
    Write-Host "Actualizando brain…"
    if (-not (Pull-Code $Dir)) { exit 1 }
    & $Uv sync --quiet
    Write-Host ("Listo: brain está en " + ((Get-Content (Join-Path $Dir "VERSION") -ErrorAction SilentlyContinue) -join ""))
    # un dashboard que ya estaba abierto sigue con el código viejo: si lo abrió este lanzador, se reinicia solo
    $port = if ($env:BRAIN_PORT) { $env:BRAIN_PORT } else { "8765" }
    try {
      Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/version" -TimeoutSec 2 | Out-Null
      $r = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$port/api/update/apply" -Headers @{ "X-Brain" = "1" } -ContentType "application/json" -Body "{}" -TimeoutSec 5
      if ($r.ok) { Write-Host "El dashboard abierto se reinicia con la versión nueva." }
      else { Write-Host "El dashboard que está abierto sigue con la versión anterior: cerralo y abrilo de nuevo." }
    } catch { }
    Write-Host "Si Claude u otro agente estaba abierto, reinicialo para usar la versión nueva."
  }
  "app" {
    # la app de escritorio (la instala install.ps1): ícono en la bandeja, avisos del sistema y notch
    $exe = @("$env:LOCALAPPDATA\brain\brain-app.exe", "$env:LOCALAPPDATA\Programs\brain\brain-app.exe") |
      Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($exe) { $env:BRAIN_HOME = $Dir; Start-Process $exe; exit 0 }
    Write-Host "La app de escritorio no está instalada."
    Write-Host "Instalala (o actualizala) con: irm https://raw.githubusercontent.com/Lautaro005/brain/main/install.ps1 | iex"
    Write-Host "O bajala del release: https://github.com/Lautaro005/brain/releases/latest"
    exit 1
  }
  "app-update" {
    # lo lanza la app de escritorio cuando su versión no es la del código: espera a que se cierre (pid), baja la app
    # del último release, la instala y la vuelve a abrir (si el release todavía no la trae, abre la que había)
    $log = Join-Path $Dir "data\logs\app.log"
    New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
    function Log($m) { Add-Content -Path $log -Value ("{0} [app-update] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m) -Encoding UTF8 }
    if ($args.Count -gt 1) { Wait-Process -Id ([int]$args[1]) -Timeout 40 -ErrorAction SilentlyContinue }
    else { Stop-Process -Name "brain-app" -ErrorAction SilentlyContinue; Start-Sleep -Seconds 3 }  # a mano: cierra la app que esté abierta
    Log "actualizando la app de escritorio"
    $env:BRAIN_APP_ONLY = "1"; $env:BRAIN_HOME = $Dir
    try { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Dir "install.ps1") *>> $log; Log "listo, abriendo la app" }
    catch { Log "no se pudo actualizar la app; abro la que estaba" }
    $exe = @("$env:LOCALAPPDATA\brain\brain-app.exe", "$env:LOCALAPPDATA\Programs\brain\brain-app.exe") |
      Where-Object { Test-Path $_ } | Select-Object -First 1
    Remove-Item Env:BRAIN_APP_ONLY -ErrorAction SilentlyContinue
    if ($exe) { $env:BRAIN_HOME = $Dir; Start-Process $exe }
    exit 0
  }
  "uninstall" {
    $shim = Join-Path $env:USERPROFILE ".local\bin\brain.cmd"
    if ((Test-Path $shim) -and (Select-String -Quiet -Path $shim -Pattern "generado por install.ps1")) { Remove-Item $shim; Write-Host "Saqué el comando brain ($shim)." }
    else { Write-Host "No encontré el comando brain instalado." }
    Write-Host "La app de escritorio (si la instalaste) se desinstala desde Configuración -> Aplicaciones."
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
  brain app             abre la app de escritorio (bandeja, avisos y notch)
  brain app-update      baja la app del último release y la vuelve a abrir
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
      if (-not (Pull-Code $Dir)) {
        Write-Host "No se pudo actualizar; vuelvo a abrir la versión instalada."; $env:BRAIN_UPDATE_FAILED = "pull"
      } else {
        & $Uv sync --quiet
        if ($LASTEXITCODE -ne 0) { Write-Host "Se bajó la versión nueva pero no se pudieron instalar las dependencias. Corré: brain update"; $env:BRAIN_UPDATE_FAILED = "sync" }
      }
      $extra = @("--no-browser")
    }
  }
}
