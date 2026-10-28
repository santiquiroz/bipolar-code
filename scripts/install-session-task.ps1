# Registra la tarea que arranca bipolar al iniciar sesión, en la sesión interactiva del usuario.
# El sandbox de Windows de Muse y Codex no funciona en la sesión 0 de la tarea con contraseña;
# esta tarea le quita el puerto a esa instancia (start-bipolar.ps1 -TomarSesion). No requiere administrador.
param(
    [string]$TaskName = 'bipolar-code backend sesion',
    [switch]$IniciarAhora
)
$ErrorActionPreference = 'Stop'

$launcher = Join-Path $PSScriptRoot 'start-bipolar.ps1'
if (-not (Test-Path $launcher)) { throw "No existe $launcher" }

$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcher`" -TomarSesion"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Force | Out-Null
Write-Host "Tarea '$TaskName': al iniciar sesión, toma el puerto desde la sesión interactiva."

if ($IniciarAhora) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Tarea '$TaskName' iniciada."
}
