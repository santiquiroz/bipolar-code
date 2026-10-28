# Lanzador del backend para la tarea programada; usa el entorno virtual de bipolar si existe.
# Debe ejecutarse desde su ubicación en el repo (scripts\), porque la raíz se resuelve desde aquí.
param(
    [string]$BindHost = '0.0.0.0',
    [int]$Port = 8000,
    [string]$LogFile = 'C:\litellm\bipolar-backend.log',
    [string]$VenvDir = 'C:\litellm\venv'
)

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $repoRoot 'backend'
$venvPython = Join-Path $VenvDir 'Scripts\python.exe'

$logDir = Split-Path -Parent $LogFile
if ($logDir -and -not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }

if (Test-Path $venvPython) {
    $python = $venvPython
} else {
    $python = 'python'
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -Path $LogFile -Value "$stamp AVISO: no existe $venvPython; se usa python del PATH. Ejecuta scripts\setup-venv.ps1."
}

Set-Location $backendDir
& $python -m uvicorn app.main:app --host $BindHost --port $Port *>> $LogFile
