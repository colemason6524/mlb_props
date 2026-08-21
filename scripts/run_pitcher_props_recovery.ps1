param(
    [string]$ProjectDir = "C:\Users\muski\mlb_props",
    [string]$PythonExe = "python"
)

$ErrorActionPreference = "Continue"

New-Item -ItemType Directory -Force -Path (Join-Path $ProjectDir "logs") | Out-Null
$LogPath = Join-Path $ProjectDir "logs\pitcher_props_recovery_task.log"

function Write-TaskLog {
    param([string]$Message)
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$stamp  $Message" | Out-File -FilePath $LogPath -Append -Encoding utf8
}

function Invoke-LoggedCommand {
    param(
        [string]$FilePath,
        [string[]]$Arguments = @()
    )
    $TempOutput = Join-Path ([System.IO.Path]::GetTempPath()) ("mlb_pitcher_recovery_{0}.log" -f [guid]::NewGuid().ToString("N"))
    try {
        Write-TaskLog "Running command: $FilePath $($Arguments -join ' ')"
        & $FilePath @Arguments > $TempOutput 2>&1
        $CommandExitCode = $LASTEXITCODE
        if (Test-Path $TempOutput) {
            Get-Content -Path $TempOutput | ForEach-Object { Write-TaskLog $_ }
        }
        return $CommandExitCode
    }
    finally {
        Remove-Item -Path $TempOutput -Force -ErrorAction SilentlyContinue
    }
}

try {
    Write-TaskLog "Starting pitcher props recovery task"
    if (-not (Test-Path $ProjectDir)) {
        throw "Project directory does not exist: $ProjectDir"
    }
    Set-Location $ProjectDir

    $env:DATA_MODE = "live"
    $env:SEND_DISCORD = "true"
    $env:EXPORT_HISTORY = "true"
    $env:RUN_NOTE = "scheduled recovery rerun"

    $ExitCode = Invoke-LoggedCommand -FilePath $PythonExe -Arguments @("run_pitcher_recovery.py")
    Write-TaskLog "Finished pitcher props recovery with exit code $ExitCode"
    exit $ExitCode
}
catch {
    Write-TaskLog "FAILED: $($_.Exception.Message)"
    if ($_.ScriptStackTrace) {
        Write-TaskLog $_.ScriptStackTrace
    }
    exit 1
}
