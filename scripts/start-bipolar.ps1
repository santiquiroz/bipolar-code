# Lanzador del backend para la tarea programada; usa el entorno virtual de bipolar (scripts\setup-venv.ps1).
# Debe ejecutarse desde su ubicación en el repo (scripts\), porque la raíz se resuelve desde aquí.
param(
    [string]$BindHost = '0.0.0.0',
    [int]$Port = 8000,
    [string]$LogFile = 'C:\litellm\bipolar-backend.log',
    [string]$VenvDir = (Join-Path $env:LOCALAPPDATA 'bipolar-code\venv'),
    # La tarea de sesión lo usa para quitarle el puerto a la instancia de la sesión 0
    [switch]$TomarSesion,
    [string]$TareaSesionCero = 'bipolar-code backend'
)

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $repoRoot 'backend'
$venvPython = Join-Path $VenvDir 'Scripts\python.exe'

$logDir = Split-Path -Parent $LogFile
if ($logDir -and -not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
# Log propio: el del backend queda abierto por la instancia en marcha y Add-Content fallaría
$launcherLog = Join-Path $logDir 'bipolar-launcher.log'

function Write-Log {
    param([string]$Message)
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -Path $launcherLog -Value "$stamp $Message" -ErrorAction SilentlyContinue
}

function Test-PortInUse {
    param([int]$Port)
    $client = [System.Net.Sockets.TcpClient]::new()
    try { $client.Connect('127.0.0.1', $Port); return $true } catch { return $false } finally { $client.Dispose() }
}

function Get-PortOwner {
    param([int]$Port)
    $listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -ne $listener) { return $listener.OwningProcess }
    # Desde la sesión interactiva, Get-NetTCPConnection no ve el proceso de la tarea de la sesión 0 (netstat da PID 0)
    if (Test-PortInUse -Port $Port) { return -1 }
    return $null
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
    $owner = if ($ownerPid -gt 0) { Get-Process -Id $ownerPid -ErrorAction SilentlyContinue } else { $null }
    # Dueño invisible (-1) = proceso de la sesión 0: los de la sesión interactiva sí se ven
    $ownerSession = if ($null -ne $owner) { $owner.SessionId } elseif ($ownerPid -eq -1) { 0 } else { -1 }
    # El sandbox de Windows de Muse y Codex no arranca en la sesión 0
    if ($TomarSesion -and $mySession -ne 0 -and $ownerSession -eq 0) {
        Write-Log "Tomando el puerto $Port desde la sesión interactiva (PID $ownerPid en sesión 0)"
        # Un proceso lanzado por la tarea con contraseña no se puede terminar desde la sesión interactiva
        # (OpenProcess da acceso denegado); el Programador de tareas sí detiene su propio árbol
        Stop-ScheduledTask -TaskName $TareaSesionCero -ErrorAction SilentlyContinue
        Wait-PortFree -Port $Port
        if ($null -ne (Get-PortOwner -Port $Port) -and $ownerPid -gt 0) {
            Stop-Process -Id $ownerPid -Force -ErrorAction SilentlyContinue
            Wait-PortFree -Port $Port
        }
        if ($null -ne (Get-PortOwner -Port $Port)) {
            Write-Log "No se pudo liberar el puerto $Port; sigue la instancia de la sesión 0"
            exit 0
        }
    } else {
        # exit 0 para que la tarea programada no entre en reintentos
        Write-Log "bipolar ya escucha en $Port (PID $ownerPid, sesión $ownerSession); no se inicia otra instancia"
        exit 0
    }
}

Write-Log "Iniciando bipolar en $BindHost`:$Port (sesión $((Get-Process -Id $PID).SessionId))"
Set-Location $backendDir
& $venvPython -m uvicorn app.main:app --host $BindHost --port $Port *>> $LogFile
