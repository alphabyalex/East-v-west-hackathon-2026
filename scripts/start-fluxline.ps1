param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$env:FLUXLINE_ALLOW_PROVISIONAL = "1"
$fluxlineRoot = Split-Path -Parent $PSScriptRoot
$fluxlinePython = Join-Path $fluxlineRoot '.venv\Scripts\python.exe'
$fluxlineWeb = Join-Path $fluxlineRoot 'web'
$fluxlineLogs = Join-Path $fluxlineRoot '.tools\app-launch'
$fluxlineNode = (Get-Command node -ErrorAction SilentlyContinue).Source
if (-not $fluxlineNode) {
    $runtimeRoot = Join-Path $env:LOCALAPPDATA 'OpenAI\Codex\runtimes\cua_node'
    if (Test-Path -LiteralPath $runtimeRoot) {
        $fluxlineNode = Get-ChildItem -Path (Join-Path $runtimeRoot '*\bin\node.exe') -File |
            Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
    }
}
if (-not $fluxlineNode) { throw 'Install Node.js 22.12 or later before opening Fluxline.' }
if (-not (Test-Path -LiteralPath $fluxlinePython)) { throw 'The local Python environment is missing. See docs/ML_WORKSPACE.md.' }
if (-not (Test-Path -LiteralPath (Join-Path $fluxlineWeb 'node_modules\vite\bin\vite.js'))) { throw 'Install frontend dependencies with npm ci in web first.' }

# The estimator remains a hidden, separate job runner with its existing lock.
& (Join-Path $PSScriptRoot 'start-ml-workspace.ps1') -NoBrowser
New-Item -ItemType Directory -Path $fluxlineLogs -Force | Out-Null
function Test-FluxlineEndpoint([string]$Url) {
    try { return (Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200 }
    catch { return $false }
}
if (-not (Test-FluxlineEndpoint 'http://127.0.0.1:8000/api/location-estimator/locations')) {
    if (Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue) {
        throw 'Port 8000 is occupied by an older or different API. Stop that API, then open Fluxline again.'
    }
    $apiProcess = Start-Process -FilePath $fluxlinePython -ArgumentList @('-m', 'uvicorn', 'api.main:app', '--host', '127.0.0.1', '--port', '8000') -WorkingDirectory $fluxlineRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $fluxlineLogs 'api.stdout.log') -RedirectStandardError (Join-Path $fluxlineLogs 'api.stderr.log') -PassThru
    Set-Content -LiteralPath (Join-Path $fluxlineLogs 'api.pid') -Value $apiProcess.Id
}
if (-not (Test-FluxlineEndpoint 'http://127.0.0.1:5174/src/main.tsx')) {
    if (Get-NetTCPConnection -State Listen -LocalPort 5174 -ErrorAction SilentlyContinue) { throw 'Port 5174 is occupied by a different app.' }
    $webProcess = Start-Process -FilePath $fluxlineNode -ArgumentList @('node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', '5174', '--strictPort') -WorkingDirectory $fluxlineWeb -WindowStyle Hidden -RedirectStandardOutput (Join-Path $fluxlineLogs 'web.stdout.log') -RedirectStandardError (Join-Path $fluxlineLogs 'web.stderr.log') -PassThru
    Set-Content -LiteralPath (Join-Path $fluxlineLogs 'web.pid') -Value $webProcess.Id
}
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    if ((Test-FluxlineEndpoint 'http://127.0.0.1:8000/api/location-estimator/locations') -and (Test-FluxlineEndpoint 'http://127.0.0.1:5174/app')) {
        Write-Output 'Fluxline is running at http://127.0.0.1:5174/app'
        if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:5174/app' }
        exit 0
    }
    Start-Sleep -Milliseconds 500
}
throw "Fluxline did not become ready. Check the logs in $fluxlineLogs."
