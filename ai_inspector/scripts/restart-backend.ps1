param([int]$Port = 8000, [int]$GeometryPort = 8001)
$ErrorActionPreference = 'Stop'
$workspaceRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$localDirectory = Join-Path $workspaceRoot '.local'
$processFile = Join-Path $localDirectory 'remote-processes.json'
$records = @(Get-Content -LiteralPath $processFile -Raw | ConvertFrom-Json)
$public = Get-Content -LiteralPath (Join-Path $localDirectory 'connection-public.json') -Raw | ConvertFrom-Json
$record = $records | Where-Object name -eq 'server'
if (-not $record) { throw 'No saved backend process. Use start-remote-test.ps1.' }
$pythonExecutable = Join-Path $workspaceRoot '.venv\Scripts\python.exe'
$process = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
if ($process) {
    if ($process.Path -ne $pythonExecutable -or $process.StartTime.ToUniversalTime().Ticks -ne ([datetime]$record.started_at).ToUniversalTime().Ticks) {
        throw 'Refusing to stop a process that does not match the saved backend.'
    }
    & taskkill.exe /PID $record.pid /T /F | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not stop the saved backend process tree.' }
}
$savedEnvironment = @{}
$names = @('INSPECTOR_PUBLIC_BASE_URL','INSPECTOR_GEOMETRY_BASE_URL','INSPECTOR_GEOMETRY_MODE','INSPECTOR_GEOMETRY_ACCEPTS_RGB_ONLY')
foreach ($name in $names) { $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    $env:INSPECTOR_PUBLIC_BASE_URL = $public.base_url
    if ($public.geometry_mode -eq 'mock') {
        $env:INSPECTOR_GEOMETRY_BASE_URL = "http://127.0.0.1:$GeometryPort"
        $env:INSPECTOR_GEOMETRY_MODE = 'mock'
        $env:INSPECTOR_GEOMETRY_ACCEPTS_RGB_ONLY = 'true'
    }
    $server = Start-Process -FilePath $pythonExecutable -ArgumentList @('-m','uvicorn','ai_inspector.main:app','--host','127.0.0.1','--port',"$Port",'--ws-max-size','65536') `
        -WorkingDirectory $workspaceRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $localDirectory 'server.stdout.log') -RedirectStandardError (Join-Path $localDirectory 'server.stderr.log')
    $newRecord = [pscustomobject]@{ name='server'; pid=$server.Id; executable=$pythonExecutable; started_at=$server.StartTime.ToUniversalTime().ToString('o') }
    $updated = @($records | Where-Object name -ne 'server') + @($newRecord)
    $updated | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $processFile -Encoding utf8
    for ($attempt=0; $attempt -lt 30; $attempt++) {
        if ($server.HasExited) { throw 'Backend exited; inspect .local/server.stderr.log.' }
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
            if ($health.status -eq 'ok') { Write-Output "Backend restarted. Public address unchanged: $($public.base_url)"; return }
        } catch { }
        Start-Sleep -Milliseconds 500
    }
    throw 'Backend did not become ready.'
} finally {
    foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process') }
}
