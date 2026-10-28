# Crea el entorno virtual de bipolar (por defecto %LOCALAPPDATA%\bipolar-code\venv) e instala sus dependencias.
# Fuera del repo a propósito: los sandboxes de Muse y Codex marcan el workspace con integridad
# baja, y un python.exe dentro de él no podría escribir en %TEMP% ni en C:\litellm. Y en una carpeta
# por usuario porque la tarea programada lo ejecuta con tu cuenta: otro usuario no debe poder reemplazarlo.
param(
    [string]$Python = 'py',
    [string]$PythonArgs = '-3.11',
    [string]$VenvDir = (Join-Path $env:LOCALAPPDATA 'bipolar-code\venv')
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $repoRoot 'backend'
$venvDir = $VenvDir
$venvPython = Join-Path $venvDir 'Scripts\python.exe'
$requirements = Join-Path $backendDir 'requirements.txt'
$requirementsLitellm = Join-Path $backendDir 'requirements-litellm.txt'

function Assert-LastExitCode {
    param([string]$Step)
    if ($LASTEXITCODE -ne 0) { throw "Error: falló '$Step' (código $LASTEXITCODE)." }
}

function New-BipolarVenv {
    if (Get-Command $Python -ErrorAction SilentlyContinue) {
        & $Python $PythonArgs -m venv $venvDir
        Assert-LastExitCode "$Python $PythonArgs -m venv"
        return
    }
    Write-Host "No se encontró '$Python'; se usa 'python' del PATH."
    & python -m venv $venvDir
    Assert-LastExitCode 'python -m venv'
}

if (Test-Path $venvPython) {
    Write-Host "El entorno virtual ya existe: $venvDir"
} else {
    Write-Host "Creando entorno virtual en $venvDir ..."
    New-BipolarVenv
}

& $venvPython -m pip install --upgrade pip
Assert-LastExitCode 'pip install --upgrade pip'

& $venvPython -m pip install -r $requirements -r $requirementsLitellm
Assert-LastExitCode 'pip install -r requirements'

Write-Host "Python del entorno virtual: $venvPython"
& (Join-Path $venvDir 'Scripts\litellm.exe') --version
Assert-LastExitCode 'litellm --version'
