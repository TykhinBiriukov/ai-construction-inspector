param(
    [switch]$MockGeometry,
    [int]$Port = 8000,
    [int]$GeometryPort = 8001
)
$ErrorActionPreference = 'Stop'
$workspaceRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$localDirectory = Join-Path $workspaceRoot '.local'
$pythonExecutable = Join-Path $workspaceRoot '.venv\Scripts\python.exe'
$tunnelExecutable = Join-Path $workspaceRoot 'tools\cloudflared.exe'
$processFile = Join-Path $localDirectory 'remote-processes.json'
if (-not (Test-Path -LiteralPath $pythonExecutable)) { throw 'Create the project virtual environment first.' }
if (-not (Test-Path -LiteralPath $tunnelExecutable)) { throw 'Install the official cloudflared binary as tools/cloudflared.exe first.' }
New-Item -ItemType Directory -Path $localDirectory -Force | Out-Null
if (Test-Path -LiteralPath $processFile) { throw 'A remote-test process record exists. Run scripts/stop-remote-test.ps1 before starting again.' }
foreach ($candidatePort in @($Port) + $(if ($MockGeometry) { @($GeometryPort) } else { @() })) {
    $portProbe = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $candidatePort)
    try { $portProbe.Start() } catch { throw "Port $candidatePort is already in use; no tunnel was started." } finally { $portProbe.Stop() }
}
$previousDirectory = Get-Location
$processRecords = @()
$savedEnvironment = @{}
$runtimeNames = @('INSPECTOR_PUBLIC_BASE_URL','INSPECTOR_GEOMETRY_BASE_URL','INSPECTOR_GEOMETRY_MODE','INSPECTOR_GEOMETRY_ACCEPTS_RGB_ONLY')
foreach ($name in $runtimeNames) { $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
function Start-ProjectHelper([string]$Name, [string]$Executable, [string[]]$HelperArguments) {
    $process = Start-Process -FilePath $Executable -ArgumentList $HelperArguments -WorkingDirectory $workspaceRoot -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $localDirectory "$Name.stdout.log") -RedirectStandardError (Join-Path $localDirectory "$Name.stderr.log") -PassThru
    $script:processRecords += [pscustomobject]@{ name=$Name; pid=$process.Id; executable=$Executable; started_at=$process.StartTime.ToUniversalTime().ToString('o') }
    $script:processRecords | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $processFile -Encoding utf8
    return $process
}
try {
    Set-Location -LiteralPath $workspaceRoot
    & $pythonExecutable scripts/prepare_remote_config.py --ensure-tokens
    if ($LASTEXITCODE -ne 0) { throw 'Could not prepare server identities.' }
    $tunnel = Start-ProjectHelper 'tunnel' $tunnelExecutable @('tunnel','--url',"http://127.0.0.1:$Port",'--protocol','http2','--no-autoupdate')
    $publicUrl = $null
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        if ($tunnel.HasExited) { throw 'Cloudflare tunnel exited; inspect .local/tunnel.stderr.log.' }
        $logFile = Join-Path $localDirectory 'tunnel.stderr.log'
        if (Test-Path -LiteralPath $logFile) {
            $logText = [string](Get-Content -LiteralPath $logFile -Raw)
            $match = [regex]::Match($logText, 'https://[a-z0-9-]+\.trycloudflare\.com')
            if ($match.Success) { $publicUrl = $match.Value; break }
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $publicUrl) { throw 'No external HTTPS address was returned by the tunnel.' }
    $env:INSPECTOR_PUBLIC_BASE_URL = $publicUrl
    if ($MockGeometry) {
        $env:INSPECTOR_GEOMETRY_BASE_URL = "http://127.0.0.1:$GeometryPort"
        $env:INSPECTOR_GEOMETRY_MODE = 'mock'
        $env:INSPECTOR_GEOMETRY_ACCEPTS_RGB_ONLY = 'true'
        $null = Start-ProjectHelper 'geometry-mock' $pythonExecutable @('-m','ai_inspector.mock_geometry','--host','127.0.0.1','--port',"$GeometryPort")
    }
    $server = Start-ProjectHelper 'server' $pythonExecutable @('-m','uvicorn','ai_inspector.main:app','--host','127.0.0.1','--port',"$Port",'--ws-max-size','65536')
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if ($server.HasExited) { throw 'Backend exited; inspect .local/server.stderr.log.' }
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
            if ($health.status -eq 'ok') { $ready = $true; break }
        } catch { }
        Start-Sleep -Milliseconds 500
    }
    if (-not $ready) { throw 'Backend did not become ready.' }
    $configArguments = @('scripts/prepare_remote_config.py','--public-url',$publicUrl)
    if ($MockGeometry) { $configArguments += '--mock-geometry' }
    & $pythonExecutable @configArguments
    if ($LASTEXITCODE -ne 0) { throw 'Could not write connection data.' }
    Write-Output "Docs: $publicUrl/docs"
    Write-Output 'Keep this computer awake. Stop helpers with scripts/stop-remote-test.ps1.'
} catch {
    foreach ($record in $processRecords) { Stop-Process -Id $record.pid -ErrorAction SilentlyContinue }
    if (Test-Path -LiteralPath $processFile) { Remove-Item -LiteralPath $processFile }
    throw
} finally {
    foreach ($name in $runtimeNames) { [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process') }
    Set-Location -LiteralPath $previousDirectory
}
