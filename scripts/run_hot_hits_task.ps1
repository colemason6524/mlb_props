param(
    [string]$ProjectDir = "C:\Users\muski\mlb_props",
    [string]$PythonExe = "python",
    [string]$DisplayLimit = "8",
    [string]$DiscordMinScore = "10",
    [switch]$ExportHistory
)

$ErrorActionPreference = "Stop"

New-Item -ItemType Directory -Force -Path (Join-Path $ProjectDir "logs") | Out-Null
$LogPath = Join-Path $ProjectDir "logs\hot_hits_task.log"

function Write-TaskLog {
    param([string]$Message)
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$stamp  $Message" | Out-File -FilePath $LogPath -Append -Encoding utf8
}

try {
    Write-TaskLog "Starting MLB hot hits task"
    Write-TaskLog "User: $env:USERNAME"
    Write-TaskLog "ProjectDir: $ProjectDir"
    Write-TaskLog "PythonExe: $PythonExe"

    if (-not (Test-Path $ProjectDir)) {
        throw "Project directory does not exist: $ProjectDir"
    }

    Set-Location $ProjectDir

    $env:DATA_MODE = "live"
    $env:SEND_DISCORD = "true"
    $env:DISPLAY_LIMIT = $DisplayLimit
    $env:HOT_HITS_DISCORD_MIN_SCORE = $DiscordMinScore
    $env:EXPORT_HISTORY = if ($ExportHistory) { "true" } else { "false" }

    if ([string]::IsNullOrWhiteSpace($env:DISCORD_WEBHOOK_URL)) {
        throw "DISCORD_WEBHOOK_URL is not available to this scheduled task user"
    }

    Write-TaskLog "Python version:"
    & $PythonExe --version *>> $LogPath

    Write-TaskLog "ExportHistory: $env:EXPORT_HISTORY"
    Write-TaskLog "Running hot hits"
    & $PythonExe run_hot_hits.py *>> $LogPath
    $ExitCode = $LASTEXITCODE
    Write-TaskLog "Finished hot hits with exit code $ExitCode"
    exit $ExitCode
}
catch {
    Write-TaskLog "FAILED: $($_.Exception.Message)"
    exit 1
}
