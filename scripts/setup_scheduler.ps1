# InboxAI - Setup Windows Task Scheduler for automatic email ingestion
# Run: powershell -ExecutionPolicy Bypass -File scripts/setup_scheduler.ps1

$TaskName = "InboxAI-EmailSync"
$ProjectDir = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$PythonExe = Join-Path $ProjectDir "venv\Scripts\python.exe"
$RunScript = Join-Path $ProjectDir "run.py"
$LogFile = Join-Path $ProjectDir "logs\ingestion.log"

# Validate paths
if (-not (Test-Path $PythonExe)) {
    Write-Error "Python not found at: $PythonExe"
    Write-Error "Make sure the venv is set up: python -m venv venv"
    exit 1
}

if (-not (Test-Path $RunScript)) {
    Write-Error "run.py not found at: $RunScript"
    exit 1
}

# Create logs directory
$LogDir = Join-Path $ProjectDir "logs"
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

# Remove existing task if present
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Removing existing task: $TaskName"
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# Create the action: run ingestion and log output
$Action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "$RunScript --ingest" `
    -WorkingDirectory $ProjectDir

# Trigger: every 6 hours, starting now
$Trigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Hours 6) `
    -RepetitionDuration (New-TimeSpan -Days 365)

# Settings: run whether logged in or not, don't stop on battery
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 5)

# Register the task (runs as current user)
Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "InboxAI: Sync emails every 6 hours (incremental)"

Write-Host ""
Write-Host "Task '$TaskName' created successfully!" -ForegroundColor Green
Write-Host "  Schedule:  Every 6 hours"
Write-Host "  Python:    $PythonExe"
Write-Host "  Script:    $RunScript --ingest"
Write-Host "  Work Dir:  $ProjectDir"
Write-Host ""
Write-Host "To verify:   Get-ScheduledTask -TaskName '$TaskName'"
Write-Host "To run now:  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "To remove:   Unregister-ScheduledTask -TaskName '$TaskName'"
