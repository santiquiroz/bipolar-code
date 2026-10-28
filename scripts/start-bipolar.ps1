# Lanzador del backend para la tarea programada; usa el entorno virtual de bipolar (scripts\setup-venv.ps1).
# Debe ejecutarse desde su ubicación en el repo (scripts\), porque la raíz se resuelve desde aquí.
param(
    [string]$BindHost = '0.0.0.0',
    [int]$Port = 8000,
    [string]$LogFile = 'C:\litellm\bipolar-backend.log',
    [string]$VenvDir = (Join-Path $env:LOCALAPPDATA 'bipolar-code\venv')
)

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $repoRoot 'backend'
$venvPython = Join-Path $VenvDir 'Scripts\python.exe'

$logDir = Split-Path -Parent $LogFile
if ($logDir -and -not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }

# Sin venv no arranca: caer al python del PATH ejecutaría cualquier intérprete con tu cuenta
if (-not (Test-Path $venvPython)) {
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -Path $LogFile -Value "$stamp ERROR: no existe $venvPython. Ejecuta scripts\setup-venv.ps1."
    exit 1
}

Set-Location $backendDir
& $venvPython -m uvicorn app.main:app --host $BindHost --port $Port *>> $LogFile
