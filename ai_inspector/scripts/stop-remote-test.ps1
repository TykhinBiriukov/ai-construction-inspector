$ErrorActionPreference = 'Stop'
$workspaceRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$processFile = Join-Path $workspaceRoot '.local\remote-processes.json'
if (-not (Test-Path -LiteralPath $processFile)) { Write-Output 'No saved remote-test helpers.'; return }
$records = @(Get-Content -LiteralPath $processFile -Raw | ConvertFrom-Json)
foreach ($record in $records) {
    $process = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
    if (-not $process) { continue }
    $expectedExecutable = [IO.Path]::GetFullPath($record.executable)
    if (-not $expectedExecutable.StartsWith($workspaceRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Refusing to stop a process outside this workspace.'
    }
    if ($process.Path -ne $expectedExecutable -or $process.StartTime.ToUniversalTime().Ticks -ne ([datetime]$record.started_at).ToUniversalTime().Ticks) {
        throw "Process $($record.pid) no longer matches the saved helper."
    }
    & taskkill.exe /PID $record.pid /T /F | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not stop saved helper $($record.name)." }
    Write-Output "Stopped $($record.name)."
}
Remove-Item -LiteralPath $processFile
