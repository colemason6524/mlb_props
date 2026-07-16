param(
    [string]$ProjectDir = "C:\Users\muski\mlb_props",
    [string]$PythonExe = "python",
    [switch]$AllHistory,
    [switch]$CoreOnly
)

$ErrorActionPreference = "Stop"

New-Item -ItemType Directory -Force -Path (Join-Path $ProjectDir "logs") | Out-Null
$LogPath = Join-Path $ProjectDir "logs\pitcher_props_backtest_task.log"

function Write-TaskLog {
    param([string]$Message)
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$stamp  $Message" | Out-File -FilePath $LogPath -Append -Encoding utf8
}

try {
    Write-TaskLog "Starting MLB pitcher props backtest task"
    Write-TaskLog "User: $env:USERNAME"
    Write-TaskLog "ProjectDir: $ProjectDir"
    Write-TaskLog "PythonExe: $PythonExe"

    if (-not (Test-Path $ProjectDir)) {
        throw "Project directory does not exist: $ProjectDir"
    }

    Set-Location $ProjectDir

    Write-TaskLog "Python version:"
    & $PythonExe --version *>> $LogPath

    $BacktestArgs = @("backtest.py")
    if ($AllHistory) {
        $BacktestArgs += "--all-history"
    }
    if ($CoreOnly) {
        $BacktestArgs += "--core-only"
    }

    Write-TaskLog "Running pitcher props backtest: $PythonExe $($BacktestArgs -join ' ')"
    & $PythonExe @BacktestArgs *>> $LogPath
    $ExitCode = $LASTEXITCODE
    Write-TaskLog "Finished pitcher props backtest with exit code $ExitCode"
    exit $ExitCode
}
catch {
    Write-TaskLog "FAILED: $($_.Exception.Message)"
    exit 1
}
