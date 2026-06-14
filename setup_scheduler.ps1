# Run this ONCE to register the daily scheduled task
# Open PowerShell as Administrator, then run this script

$ScriptPath = "C:\SAFEAGENT\safeagent_monitor.ps1"
$TaskName   = "SafeAgentMonitor"
$RunAt      = "08:00"  # 8am daily — change if you prefer a different time

# Copy monitor script to SAFEAGENT folder if not already there
if (-not (Test-Path $ScriptPath)) {
    Write-Host "ERROR: $ScriptPath not found. Copy safeagent_monitor.ps1 there first." -ForegroundColor Red
    exit 1
}

# Remove existing task if present
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed existing task." -ForegroundColor Yellow
}

$action  = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NonInteractive -ExecutionPolicy Bypass -File `"$ScriptPath`""

$trigger = New-ScheduledTaskTrigger -Daily -At $RunAt

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -RestartCount 1 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable  # runs on next opportunity if machine was off at trigger time

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "SafeAgent distribution monitor — scans GitHub and Reddit for duplicate execution pain cases" `
    -RunLevel Highest | Out-Null

Write-Host "Task '$TaskName' registered. Runs daily at $RunAt." -ForegroundColor Green
Write-Host "Results saved to C:\SAFEAGENT\monitor\cases.json" -ForegroundColor Green
Write-Host ""
Write-Host "To run immediately: Start-ScheduledTask -TaskName '$TaskName'" -ForegroundColor Cyan
Write-Host "To check status:    Get-ScheduledTask -TaskName '$TaskName'" -ForegroundColor Cyan
Write-Host "To remove:          Unregister-ScheduledTask -TaskName '$TaskName'" -ForegroundColor Cyan
