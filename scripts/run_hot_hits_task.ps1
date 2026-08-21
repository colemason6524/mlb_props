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

    if ($ExitCode -eq 0 -and $env:EXPORT_HISTORY -eq "true") {
        $HistoryDir = Join-Path $ProjectDir "outputs\history"
        $HistoryFiles = @(Get-ChildItem -LiteralPath $HistoryDir -Filter "hot_hits_*.json" -File -ErrorAction SilentlyContinue)
        if ($HistoryFiles.Count -gt 0) {
            foreach ($BackupDir in @(
                (Join-Path (Join-Path $ProjectDir "..") "mlb_props_history_backup\hot_hits"),
                (Join-Path "C:\Users\muski\iCloudDrive\mlb_props_logs" "hot_hits")
            )) {
                try {
                    New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
                    Copy-Item -LiteralPath $HistoryFiles.FullName -Destination $BackupDir -Force
                    Write-TaskLog "History backup: $($HistoryFiles.Count) files copied to $BackupDir"
                } catch {
                    Write-TaskLog "History backup skipped for $BackupDir : $($_.Exception.Message)"
                }
            }
        } else {
            Write-TaskLog "History backup skipped: no hot_hits_*.json found in $HistoryDir"
        }
    }

    exit $ExitCode
}
catch {
    Write-TaskLog "FAILED: $($_.Exception.Message)"
    exit 1
}
