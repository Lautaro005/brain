# Instalador de brain para Windows. Uso (en PowerShell):
#   irm https://raw.githubusercontent.com/Lautaro005/brain/main/install.ps1 | iex
# O con el instalador brain-setup.exe de la página de releases, que corre este mismo script.
#
# Deja la app en %USERPROFILE%\.brain (tus datos quedan ahí, en vault\ y data\) y el comando `brain`
# en %USERPROFILE%\.local\bin, que se agrega al PATH del usuario. Correrlo de nuevo actualiza.
# Variables opcionales: BRAIN_HOME, BRAIN_REPO, BRAIN_BRANCH, BRAIN_BIN, BRAIN_SKIP_OLLAMA=1, BRAIN_SKIP_CHROMIUM=1.
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Or($a, $b) { if ($a) { $a } else { $b } }
$Repo   = Or $env:BRAIN_REPO "https://github.com/Lautaro005/brain"
$Branch = Or $env:BRAIN_BRANCH "main"
$Dir    = Or $env:BRAIN_HOME (Join-Path $env:USERPROFILE ".brain")
$Bin    = Or $env:BRAIN_BIN (Join-Path $env:USERPROFILE ".local\bin")

function Say($m)  { Write-Host "==> $m" -ForegroundColor White }
function Warn($m) { Write-Host "!  $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "x  $m" -ForegroundColor Red; exit 1 }
function Has($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function Refresh-Path { $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User") }

# ---------- git ----------
if (-not (Has git)) {
  if (Has winget) {
    Say "Instalando git con winget…"
    winget install --id Git.Git -e --silent --accept-package-agreements --accept-source-agreements | Out-Null
    Refresh-Path
  }
  if (-not (Has git)) { Fail "Falta git. Instalalo desde https://git-scm.com/download/win y volvé a correr este comando." }
}

# ---------- uv (maneja Python y dependencias) ----------
$Uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $Uv) { $Uv = Join-Path $env:USERPROFILE ".local\bin\uv.exe" }
if (-not (Test-Path $Uv)) {
  Say "Instalando uv…"
  powershell -ExecutionPolicy ByPass -NoProfile -Command "irm https://astral.sh/uv/install.ps1 | iex" | Out-Null
  $Uv = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
  if (-not (Test-Path $Uv)) { Fail "No se pudo instalar uv. Mirá https://docs.astral.sh/uv/getting-started/installation/" }
}

# ---------- código ----------
if (Test-Path (Join-Path $Dir ".git")) {
  Say "Actualizando brain en $Dir"
  git -C $Dir pull --ff-only --quiet
} elseif (Test-Path $Dir) {
  Fail "$Dir ya existe y no es una instalación de brain. Movelo o usá `$env:BRAIN_HOME con otra carpeta."
} else {
  Say "Descargando brain en $Dir"
  git clone --quiet --depth 1 --branch $Branch $Repo $Dir
}
Set-Location $Dir

Say "Instalando dependencias (la primera vez tarda un par de minutos)…"
& $Uv sync --quiet
if ($LASTEXITCODE -ne 0) { Fail "Falló uv sync." }

if (-not $env:BRAIN_SKIP_CHROMIUM) {
  Say "Instalando Chromium para scrapear sitios con JavaScript…"
  & $Uv run --quiet playwright install chromium *> $null
  if ($LASTEXITCODE -ne 0) { Warn "No se pudo instalar Chromium; después: cd $Dir; uv run playwright install chromium" }
}

# ---------- Ollama (embeddings) ----------
$chatModel = $true
if (-not $env:BRAIN_SKIP_OLLAMA) {
  $Ollama = (Get-Command ollama -ErrorAction SilentlyContinue).Source
  $Default = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
  if (-not $Ollama -and (Test-Path $Default)) { $Ollama = $Default }
  if (-not $Ollama -and (Has winget)) {
    Say "Instalando Ollama con winget…"
    winget install --id Ollama.Ollama -e --silent --accept-package-agreements --accept-source-agreements | Out-Null
    Refresh-Path
    if (Test-Path $Default) { $Ollama = $Default }
  }
  if ($Ollama) {
    $started = $null
    try { Invoke-RestMethod http://127.0.0.1:11434/api/version -TimeoutSec 2 | Out-Null } catch {
      $started = Start-Process $Ollama -ArgumentList "serve" -WindowStyle Hidden -PassThru
      foreach ($i in 1..10) { try { Invoke-RestMethod http://127.0.0.1:11434/api/version -TimeoutSec 2 | Out-Null; break } catch { Start-Sleep 1 } }
    }
    $list = (& $Ollama list 2>$null) -join "`n"
    if ($list -match "(?m)^nomic-embed-text") { Say "Modelo de embeddings ya instalado" }
    else {
      Say "Bajando el modelo de embeddings (nomic-embed-text, ~270 MB)…"
      & $Ollama pull nomic-embed-text *> $null
      if ($LASTEXITCODE -ne 0) { Warn "No se pudo bajar el modelo; después: ollama pull nomic-embed-text" }
    }
    $models = (& $Ollama list 2>$null | Select-Object -Skip 1 | ForEach-Object { ($_ -split "\s+")[0] }) | Where-Object { $_ -and $_ -notmatch "embed" }
    if (-not $models) { $chatModel = $false }
    if ($started) { Stop-Process -Id $started.Id -ErrorAction SilentlyContinue }
  } else {
    Warn "No encontré Ollama. Bajalo de https://ollama.com/download y después corré: ollama pull nomic-embed-text"
  }
}

# ---------- comando `brain` ----------
New-Item -ItemType Directory -Force -Path $Bin | Out-Null
$shim = Join-Path $Bin "brain.cmd"
@"
@echo off
rem comando de brain (generado por install.ps1)
powershell -NoProfile -ExecutionPolicy Bypass -File "$Dir\brain.ps1" %*
"@ | Set-Content -Encoding ASCII $shim

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
$onPath = ($userPath -split ";") -contains $Bin
if (-not $onPath) { [Environment]::SetEnvironmentVariable("Path", ($userPath.TrimEnd(";") + ";" + $Bin).TrimStart(";"), "User") }

Write-Host ""
Write-Host "OK brain instalado en $Dir" -ForegroundColor Green
Write-Host ""
if (-not $onPath) { Write-Host "  Abrí una terminal nueva y escribí:" } else { Write-Host "  Escribí:" }
Write-Host ""
Write-Host "    brain            abre el dashboard"
Write-Host "    brain update     actualiza a la última versión"
Write-Host "    brain help       todos los comandos"
Write-Host ""
Write-Host "  Primer paso: en el dashboard, andá a Conectar agente y conectá Claude o ChatGPT."
if (-not $chatModel) {
  Write-Host ""
  Write-Host "  Opcional: ollama pull llama3.2 (~2 GB) activa el Chat, los resúmenes de fuentes"
  Write-Host "  y las entidades del grafo. Sin él, todo lo demás funciona igual."
}
Write-Host ""
