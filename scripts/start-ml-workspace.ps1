param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$workspaceRoot = Split-Path -Parent $PSScriptRoot
$workspacePython = Join-Path $workspaceRoot '.venv\Scripts\python.exe'
$workspaceUrl = 'http://127.0.0.1:8765'
if (-not (Test-Path -LiteralPath $workspacePython)) {
    throw 'The local Python environment is missing. Follow docs/ML_WALKTHROUGH.md to install it first.'
}
function Test-Workspace {
    try {
        $health = Invoke-RestMethod -Uri "$workspaceUrl/health" -TimeoutSec 2
        return $health.app -eq 'headroom-ml-workspace'
    } catch { return $false }
}
if (-not (Test-Workspace)) {
    $workspaceLogs = Join-Path $workspaceRoot 'data\processed\workbench'
    New-Item -ItemType Directory -Path $workspaceLogs -Force | Out-Null
    $workspaceProcess = Start-Process -FilePath $workspacePython -ArgumentList '-u', '-m', 'pipeline.workbench' -WorkingDirectory $workspaceRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $workspaceLogs 'server.log') -RedirectStandardError (Join-Path $workspaceLogs 'server-error.log') -PassThru
    $workspaceReady = $false
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        Start-Sleep -Milliseconds 500
        if ($workspaceProcess.HasExited) { break }
        if (Test-Workspace) { $workspaceReady = $true; break }
    }
    if (-not $workspaceReady) {
        throw "The ML workspace did not start. Check $workspaceLogs\server-error.log. Port 8765 may already be in use."
    }
    Set-Content -LiteralPath (Join-Path $workspaceLogs 'server.pid') -Value $workspaceProcess.Id
}
Write-Output "Your ML workspace is running at $workspaceUrl"
if (-not $NoBrowser) { Start-Process $workspaceUrl }
