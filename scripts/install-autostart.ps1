# Registers bipolar-code to start with Windows (before any login) and blocks its port from the LAN while keeping WSL access.
# Two tasks: the password task covers boot without a session (session 0); the session task (install-session-task.ps1)
# takes over the port at logon, because the Windows sandbox of Muse and Codex needs an interactive session.
# Run once in an elevated PowerShell: powershell -ExecutionPolicy Bypass -File scripts\install-autostart.ps1
param(
    [string]$Launcher = 'C:\litellm\start-bipolar.ps1',
    [string]$TaskName = 'bipolar-code backend',
    [int]$Port = 8000
)
$ErrorActionPreference = 'Stop'

function Assert-Admin {
    $id = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not $id.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Ejecuta este script en PowerShell como administrador.'
    }
}

function Set-PortFirewall {
    param([int]$Port)
    $name = "bipolar-code $Port solo local y WSL"
    Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    # A block rule wins over the existing allow-all rules for python.exe.
    New-NetFirewallRule -DisplayName $name -Direction Inbound -Protocol TCP -LocalPort $Port -Action Block `
        -RemoteAddress @('0.0.0.0-126.255.255.255', '128.0.0.0-172.15.255.255', '172.32.0.0-255.255.255.255') | Out-Null
    Write-Host "Firewall: puerto $Port bloqueado salvo 127.0.0.0/8 y 172.16.0.0/12 (WSL)."
}

function Register-BipolarTask {
    param([string]$TaskName, [string]$Launcher)
    $user = "$env:USERDOMAIN\$env:USERNAME"
    $cred = Get-Credential -UserName $user -Message 'Contraseña de Windows para que bipolar arranque sin iniciar sesión'
    if ($null -eq $cred) { throw 'Cancelado: sin contraseña no se registra la tarea.' }
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Launcher`""
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
        -User $cred.UserName -Password $cred.GetNetworkCredential().Password -RunLevel Limited -Force | Out-Null
    Write-Host "Tarea '$TaskName': al encender, con reinicio ante fallos."
}

function Restart-Bipolar {
    param([string]$TaskName, [int]$Port)
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($procId in ($listeners | Select-Object -ExpandProperty OwningProcess -Unique)) {
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
    Start-ScheduledTask -TaskName $TaskName
    Write-Host 'bipolar reiniciado con la tarea nueva.'
}

Assert-Admin
if (-not (Test-Path $Launcher)) { throw "No existe $Launcher" }
Set-PortFirewall -Port $Port
Register-BipolarTask -TaskName $TaskName -Launcher $Launcher
Restart-Bipolar -TaskName $TaskName -Port $Port
# Con la sesión abierta, la tarea de sesión le quita el puerto a la instancia de la sesión 0 ya mismo
& (Join-Path $PSScriptRoot 'install-session-task.ps1') -IniciarAhora
Write-Host 'Listo. Puedes cerrar esta ventana.'
Read-Host 'Enter para cerrar'
