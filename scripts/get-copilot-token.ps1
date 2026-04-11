$clientId = "01ab8ac9400c4e429b23"

# Paso 1: solicitar device code
$deviceRaw = curl.exe -s -X POST "https://github.com/login/device/code" `
    -H "Accept: application/json" `
    -H "Content-Type: application/x-www-form-urlencoded" `
    --data-urlencode "client_id=$clientId" `
    --data-urlencode "scope=read:user copilot"

$device = $deviceRaw | ConvertFrom-Json

Write-Host ""
Write-Host "=== GitHub Copilot OAuth ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "1. Abre este link en tu navegador:" -ForegroundColor White
Write-Host "   $($device.verification_uri)" -ForegroundColor Yellow
Write-Host ""
Write-Host "2. Ingresa este codigo:" -ForegroundColor White
Write-Host "   $($device.user_code)" -ForegroundColor Green
Write-Host ""
Write-Host "Esperando autorizacion..." -ForegroundColor White

$interval = if ($device.interval) { $device.interval } else { 5 }
$expires  = if ($device.expires_in) { $device.expires_in } else { 900 }
$deadline = (Get-Date).AddSeconds($expires)
$oauthToken = $null

while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds $interval

    $tokenRaw  = curl.exe -s -X POST "https://github.com/login/oauth/access_token" `
        -H "Accept: application/json" `
        -H "Content-Type: application/x-www-form-urlencoded" `
        --data-urlencode "client_id=$clientId" `
        --data-urlencode "device_code=$($device.device_code)" `
        --data-urlencode "grant_type=urn:ietf:params:oauth:grant-type:device_code"

    $tokenResp = $tokenRaw | ConvertFrom-Json
    if ($tokenResp.access_token) {
        $oauthToken = $tokenResp.access_token
        break
    }
    Write-Host "." -NoNewline
}

Write-Host ""

if (-not $oauthToken) {
    Write-Host "Tiempo agotado. Ejecuta el script de nuevo." -ForegroundColor Red
    exit 1
}

# Paso 2: obtener token de sesion de Copilot
$sessionRaw = curl.exe -s "https://api.github.com/copilot_internal/v2/token" `
    -H "Authorization: Bearer $oauthToken" `
    -H "Editor-Version: vscode/1.85.0" `
    -H "Editor-Plugin-Version: copilot-chat/0.22.0" `
    -H "User-Agent: GithubCopilot/1.138.0"

$session = $sessionRaw | ConvertFrom-Json

# Mostrar tokens en texto plano para copiar
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  TOKENS OBTENIDOS — copia estos valores" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "GITHUB_OAUTH_TOKEN=$oauthToken" -ForegroundColor Green
if ($session.token) {
    Write-Host "COPILOT_SESSION_TOKEN=$($session.token)" -ForegroundColor Green
}
Write-Host ""
Write-Host "Pega estos valores en tu archivo .env" -ForegroundColor White
Write-Host "(ubicacion por defecto: C:\litellm\.env)" -ForegroundColor Gray

# Guardar automaticamente si el .env existe
$envDir  = if ($env:LITELLM_CONFIG_DIR) { $env:LITELLM_CONFIG_DIR } else { "C:\litellm" }
$envFile = Join-Path $envDir ".env"

if (Test-Path $envFile) {
    $lines  = Get-Content $envFile | Where-Object { $_ -notmatch "^GITHUB_OAUTH_TOKEN=" -and $_ -notmatch "^COPILOT_SESSION_TOKEN=" }
    $lines += "GITHUB_OAUTH_TOKEN=$oauthToken"
    if ($session.token) {
        $lines += "COPILOT_SESSION_TOKEN=$($session.token)"
    }
    $lines | Set-Content $envFile
    Write-Host ""
    Write-Host "Guardado automaticamente en: $envFile" -ForegroundColor DarkGray
}
