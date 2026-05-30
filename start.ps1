param(
    [switch]$SkipBrowser,
    [switch]$SkipInstall,
    [switch]$SetupOnly
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Resolve-RequiredCommand {
    param(
        [string[]]$Names,
        [string]$InstallHint
    )

    foreach ($name in $Names) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command) {
            return $command.Source
        }
    }

    throw $InstallHint
}

function Invoke-Checked {
    param(
        [string]$FilePath,
        [string[]]$Arguments
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed: $FilePath $($Arguments -join ' ')"
    }
}

function Test-PortInUse {
    param([int]$Port)

    try {
        $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        return $null -ne $connection
    }
    catch {
        return $false
    }
}

$npm = Resolve-RequiredCommand `
    -Names @("npm.cmd", "npm") `
    -InstallHint "Node.js/npm was not found in PATH. Install Node.js LTS, then run start.bat again."

$venvDir = Join-Path $Root ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    $python = Resolve-RequiredCommand `
        -Names @("py.exe", "py", "python.exe", "python") `
        -InstallHint "Python 3 was not found in PATH. Install Python 3, then run start.bat again."

    Write-Step "Creating Python virtual environment"
    $pythonName = [System.IO.Path]::GetFileNameWithoutExtension($python)
    if ($pythonName -eq "py") {
        Invoke-Checked $python @("-3", "-m", "venv", $venvDir)
    }
    else {
        Invoke-Checked $python @("-m", "venv", $venvDir)
    }
}

$venvScripts = Join-Path $venvDir "Scripts"
$env:VIRTUAL_ENV = $venvDir
$env:PATH = "$venvScripts;$env:PATH"

if (-not $SkipInstall) {
    $requirements = Join-Path $Root "requirements.txt"
    $requirementsStamp = Join-Path $venvDir ".requirements.stamp"

    if (Test-Path $requirements) {
        $needsPythonInstall = -not (Test-Path $requirementsStamp)
        if (-not $needsPythonInstall) {
            $needsPythonInstall = (Get-Item $requirements).LastWriteTimeUtc -gt (Get-Item $requirementsStamp).LastWriteTimeUtc
        }

        if ($needsPythonInstall) {
            Write-Step "Installing Python dependencies"
            Invoke-Checked $venvPython @("-m", "pip", "install", "--upgrade", "pip")
            Invoke-Checked $venvPython @("-m", "pip", "install", "-r", $requirements)
            Set-Content -Path $requirementsStamp -Value (Get-Date -Format o) -Encoding ASCII
        }
    }

    $concurrentlyBin = Join-Path $Root "node_modules\.bin\concurrently.cmd"
    if (-not (Test-Path $concurrentlyBin)) {
        Write-Step "Installing root Node dependencies"
        Invoke-Checked $npm @("install")
    }

    $viteBin = Join-Path $Root "frontend\node_modules\.bin\vite.cmd"
    if (-not (Test-Path $viteBin)) {
        Write-Step "Installing frontend Node dependencies"
        Invoke-Checked $npm @("--prefix", "frontend", "install")
    }
}

$envFile = Join-Path $Root ".env"
$envExample = Join-Path $Root ".env.example"
if ((-not (Test-Path $envFile)) -and (Test-Path $envExample)) {
    Write-Step "Creating .env from .env.example"
    Copy-Item $envExample $envFile
    Write-Warning "Edit .env and set CANVAS_API_TOKEN before syncing Canvas data."
}

if ($SetupOnly) {
    Write-Step "Setup complete"
    exit 0
}

foreach ($port in @(8000, 5173)) {
    if (Test-PortInUse $port) {
        Write-Warning "Port $port is already in use. Startup may fail unless it is this app already running."
    }
}

Write-Step "Starting Canvas_helper"
Write-Host "Frontend: http://127.0.0.1:5173"
Write-Host "Backend:  http://127.0.0.1:8000/api/health"
Write-Host "Press Ctrl+C in this window to stop."

if (-not $SkipBrowser) {
    $openBrowser = "Start-Sleep -Seconds 4; Start-Process 'http://127.0.0.1:5173'"
    Start-Process -FilePath "powershell.exe" -WindowStyle Hidden -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $openBrowser) | Out-Null
}

& $npm "run" "dev"
exit $LASTEXITCODE
