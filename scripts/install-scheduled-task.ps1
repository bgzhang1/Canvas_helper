param(
    [string]$TaskName = "CanvasHelper",
    [ValidateSet("Logon", "Daily")]
    [string]$Trigger = "Logon",
    [string]$At = "09:00",
    [switch]$StartNow,
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $ScriptDir
$StartScript = Join-Path $Root "start.ps1"

if (-not (Test-Path $StartScript)) {
    throw "start.ps1 was not found at $StartScript"
}

function New-TaskTrigger {
    if ($Trigger -eq "Logon") {
        return New-ScheduledTaskTrigger -AtLogOn
    }

    try {
        $time = [datetime]::ParseExact($At, "HH:mm", [System.Globalization.CultureInfo]::InvariantCulture)
    }
    catch {
        throw "Use -At in HH:mm format, for example -At 09:00"
    }

    return New-ScheduledTaskTrigger -Daily -At $time
}

$arguments = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-WindowStyle", "Hidden",
    "-File", "`"$StartScript`"",
    "-SkipBrowser"
)

if ($SkipInstall) {
    $arguments += "-SkipInstall"
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument ($arguments -join " ") `
    -WorkingDirectory $Root

$triggerObject = New-TaskTrigger
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

$principalUser = if ($env:USERDOMAIN) { "$env:USERDOMAIN\$env:USERNAME" } else { $env:USERNAME }
$principal = New-ScheduledTaskPrincipal `
    -UserId $principalUser `
    -LogonType Interactive `
    -RunLevel LeastPrivilege

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $triggerObject `
    -Settings $settings `
    -Principal $principal `
    -Description "Starts Canvas_helper from $Root." `
    -Force | Out-Null

Write-Host "Scheduled task installed: $TaskName"
Write-Host "Root: $Root"
Write-Host "Trigger: $Trigger"

if ($StartNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Scheduled task started."
}
