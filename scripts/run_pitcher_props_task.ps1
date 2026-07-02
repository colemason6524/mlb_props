param(
    [string]$ProjectDir = "C:\Users\muski\mlb_props",
    [string]$PythonExe = "python",
    [string]$DisplayLimit = "30",
    [string]$DiscordCoreLimit = "5",
    [string]$DiscordWatchLimit = "5",
    [string]$RunNote = "scheduled full pregame run",
    [switch]$ExportHistory
)

$ErrorActionPreference = "Stop"

New-Item -ItemType Directory -Force -Path (Join-Path $ProjectDir "logs") | Out-Null
$LogPath = Join-Path $ProjectDir "logs\pitcher_props_task.log"

function Write-TaskLog {
    param([string]$Message)
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$stamp  $Message" | Out-File -FilePath $LogPath -Append -Encoding utf8
}

try {
    Write-TaskLog "Starting MLB pitcher props task"
    Write-TaskLog "User: $env:USERNAME"
    Write-TaskLog "ProjectDir: $ProjectDir"
    Write-TaskLog "PythonExe: $PythonExe"

    if (-not (Test-Path $ProjectDir)) {
        throw "Project directory does not exist: $ProjectDir"
    }

    Set-Location $ProjectDir

    $env:DATA_MODE = "live"
    $env:SEND_DISCORD = "true"
    $env:EXPORT_HISTORY = if ($ExportHistory) { "true" } else { "false" }
    $env:DISPLAY_LIMIT = $DisplayLimit
    $env:PITCHER_PROPS_DISCORD_CORE_LIMIT = $DiscordCoreLimit
    $env:PITCHER_PROPS_DISCORD_WATCH_LIMIT = $DiscordWatchLimit
    $env:RUN_NOTE = $RunNote

    if (
        [string]::IsNullOrWhiteSpace($env:PITCHER_PROPS_DISCORD_WEBHOOK_URL) -and
        [string]::IsNullOrWhiteSpace($env:DISCORD_WEBHOOK_URL)
    ) {
        throw "PITCHER_PROPS_DISCORD_WEBHOOK_URL or DISCORD_WEBHOOK_URL is not available to this scheduled task user"
    }

    Write-TaskLog "Python version:"
    & $PythonExe --version *>> $LogPath

    Write-TaskLog "ExportHistory: $env:EXPORT_HISTORY"
    Write-TaskLog "DisplayLimit: $env:DISPLAY_LIMIT"
    Write-TaskLog "DiscordCoreLimit: $env:PITCHER_PROPS_DISCORD_CORE_LIMIT"
    Write-TaskLog "DiscordWatchLimit: $env:PITCHER_PROPS_DISCORD_WATCH_LIMIT"
    Write-TaskLog "RunNote: $env:RUN_NOTE"
    Write-TaskLog "Running pitcher props"
    & $PythonExe run_nightly.py *>> $LogPath
    $ExitCode = $LASTEXITCODE
    Write-TaskLog "Finished pitcher props with exit code $ExitCode"
    exit $ExitCode
}
catch {
    Write-TaskLog "FAILED: $($_.Exception.Message)"
    exit 1
}
