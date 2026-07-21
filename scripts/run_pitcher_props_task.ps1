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

function Write-TaskLogBlock {
    param([string]$Message)
    if ([string]::IsNullOrWhiteSpace($Message)) {
        return
    }
    $Message -split "`r?`n" | ForEach-Object {
        if (-not [string]::IsNullOrWhiteSpace($_)) {
            Write-TaskLog $_
        }
    }
}

function Invoke-LoggedCommand {
    param(
        [string]$FilePath,
        [string[]]$Arguments = @()
    )
    $SafeName = [System.IO.Path]::GetFileNameWithoutExtension($FilePath) -replace "[^A-Za-z0-9_-]", "_"
    $TempOutput = Join-Path ([System.IO.Path]::GetTempPath()) ("mlb_pitcher_props_{0}_{1}.log" -f $SafeName, [guid]::NewGuid().ToString("N"))
    try {
        Write-TaskLog "Running command: $FilePath $($Arguments -join ' ')"
        & $FilePath @Arguments > $TempOutput 2>&1
        $CommandExitCode = $LASTEXITCODE
        if (Test-Path $TempOutput) {
            Get-Content -Path $TempOutput | ForEach-Object {
                Write-TaskLog $_
            }
        }
        return $CommandExitCode
    }
    finally {
        Remove-Item -Path $TempOutput -Force -ErrorAction SilentlyContinue
    }
}

function Write-FailureDetails {
    param($ErrorRecord)
    Write-TaskLog "FAILED: $($ErrorRecord.Exception.Message)"
    Write-TaskLogBlock (($ErrorRecord | Format-List * -Force | Out-String).TrimEnd())
    if ($ErrorRecord.ScriptStackTrace) {
        Write-TaskLogBlock ("Script stack trace:`n$($ErrorRecord.ScriptStackTrace)")
    }
    if ($ErrorRecord.InvocationInfo -and $ErrorRecord.InvocationInfo.PositionMessage) {
        Write-TaskLogBlock ("Invocation:`n$($ErrorRecord.InvocationInfo.PositionMessage)")
    }
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
    $VersionExitCode = Invoke-LoggedCommand -FilePath $PythonExe -Arguments @("--version")
    if ($VersionExitCode -ne 0) {
        throw "Python version check failed with exit code $VersionExitCode"
    }

    Write-TaskLog "ExportHistory: $env:EXPORT_HISTORY"
    Write-TaskLog "DisplayLimit: $env:DISPLAY_LIMIT"
    Write-TaskLog "DiscordCoreLimit: $env:PITCHER_PROPS_DISCORD_CORE_LIMIT"
    Write-TaskLog "DiscordWatchLimit: $env:PITCHER_PROPS_DISCORD_WATCH_LIMIT"
    Write-TaskLog "RunNote: $env:RUN_NOTE"
    Write-TaskLog "Running pitcher props"
    $ExitCode = Invoke-LoggedCommand -FilePath $PythonExe -Arguments @("run_nightly.py")
    Write-TaskLog "Finished pitcher props with exit code $ExitCode"
    exit $ExitCode
}
catch {
    Write-FailureDetails $_
    exit 1
}
