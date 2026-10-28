# Lanzador del backend para la tarea programada; usa el entorno virtual de bipolar (scripts\setup-venv.ps1).
# Debe ejecutarse desde su ubicación en el repo (scripts\), porque la raíz se resuelve desde aquí.
param(
    [string]$BindHost = '0.0.0.0',
    [int]$Port = 8000,
    [string]$LogFile = 'C:\litellm\bipolar-backend.log',
    [string]$VenvDir = (Join-Path $env:LOCALAPPDATA 'bipolar-code\venv'),
    # La tarea de sesión lo usa para quitarle el puerto a la instancia de la sesión 0
    [switch]$TomarSesion
)

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $repoRoot 'backend'
$venvPython = Join-Path $VenvDir 'Scripts\python.exe'

$logDir = Split-Path -Parent $LogFile
if ($logDir -and -not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }

function Write-Log {
    param([string]$Message)
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -Path $LogFile -Value "$stamp $Message"
}

function Get-PortOwner {
    param([int]$Port)
    $listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $listener) { return $null }
    return $listener.OwningProcess
}

function Wait-PortFree {
    param([int]$Port, [int]$Seconds = 20)
    for ($i = 0; $i -lt $Seconds; $i++) {
        if ($null -eq (Get-PortOwner -Port $Port)) { return }
        Start-Sleep -Seconds 1
    }
}

# Sin venv no arranca: caer al python del PATH ejecutaría cualquier intérprete con tu cuenta
if (-not (Test-Path $venvPython)) {
    Write-Log "ERROR: no existe $venvPython. Ejecuta scripts\setup-venv.ps1."
    exit 1
}

$ownerPid = Get-PortOwner -Port $Port
if ($null -ne $ownerPid) {
    $mySession = (Get-Process -Id $PID).SessionId
    $owner = Get-Process -Id $ownerPid -ErrorAction SilentlyContinue
    $ownerSession = if ($null -ne $owner) { $owner.SessionId } else { -1 }
    # El sandbox de Windows de Muse y Codex no arranca en la sesión 0
    if ($TomarSesion -and $mySession -ne 0 -and $ownerSession -eq 0) {
        Write-Log "Tomando el puerto $Port desde la sesión interactiva (PID $ownerPid en sesión 0)"
        Stop-Process -Id $ownerPid -Force
        Wait-PortFree -Port $Port
    } else {
        # exit 0 para que la tarea programada no entre en reintentos
        Write-Log "bipolar ya escucha en $Port (PID $ownerPid, sesión $ownerSession); no se inicia otra instancia"
        exit 0
    }
}

Set-Location $backendDir
& $venvPython -m uvicorn app.main:app --host $BindHost --port $Port *>> $LogFile
