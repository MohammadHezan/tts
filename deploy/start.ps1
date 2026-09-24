# Starts the Meeting Interpreter on Windows (run by "Start Interpreter.bat").
# Windows PowerShell 5.1 compatible - no PowerShell 7 syntax.
#
# Starts Docker Desktop if needed, gives Docker enough memory, lets phones on
# the Wi-Fi reach this PC, then runs docker-compose.yml and opens the page.
param([switch]$NoBrowser)

$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$Port = 8765
$FirewallRule = 'Meeting Interpreter (phones)'

function Say([string]$Message) { Write-Host $Message }
function Ask([string]$Question) {
    $answer = Read-Host "$Question [Y/n]"
    return ($answer -eq '' -or $answer -match '^[Yy]')
}
function DockerRunning {
    # Local override: with 'Stop', PowerShell 5.1 turns a native command's
    # redirected stderr ("daemon not running") into a terminating error.
    $ErrorActionPreference = 'Continue'
    & docker info *> $null
    return ($LASTEXITCODE -eq 0)
}
function WaitForDocker([int]$Seconds) {
    for ($i = 0; $i -lt ($Seconds / 5); $i++) {
        if (DockerRunning) { return $true }
        Start-Sleep -Seconds 5
    }
    return (DockerRunning)
}
function DockerDesktopExe { Join-Path $Env:ProgramFiles 'Docker\Docker\Docker Desktop.exe' }

# 1. Docker Desktop installed and running
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Say 'Docker Desktop is not installed. Opening its download page.'
    Say 'Install it, restart the PC, then double-click "Start Interpreter" again.'
    Start-Process 'https://www.docker.com/products/docker-desktop/'
    exit 1
}
if (-not (DockerRunning)) {
    if (Test-Path (DockerDesktopExe)) {
        Say 'Starting Docker Desktop...'
        Start-Process (DockerDesktopExe)
    }
    if (-not (WaitForDocker 300)) {
        Say 'Docker Desktop did not start. Open it, wait until it says "Engine running", then try again.'
        exit 1
    }
}

# 2. Memory. Docker Desktop on Windows gets half the PC's memory by default
#    (a WSL limit); the interpreter needs about 10 GB.
$dockerMem = [int64](& docker info --format '{{.MemTotal}}')
$pcGb = [math]::Floor((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
if ($dockerMem -lt 10GB) {
    $dockerGb = [math]::Round($dockerMem / 1GB, 1)
    $wslConfig = Join-Path $Env:UserProfile '.wslconfig'
    $targetGb = [math]::Min($pcGb - 4, 16)
    if ($targetGb -ge 10 -and -not (Test-Path $wslConfig)) {
        Say "Docker can use only $dockerGb GB of this PC's $pcGb GB. The interpreter needs about 10 GB."
        if (Ask "Give Docker $targetGb GB? (restarts Docker Desktop)") {
            Set-Content -Path $wslConfig -Value "[wsl2]`r`nmemory=${targetGb}GB`r`n" -Encoding ASCII
            Say 'Restarting Docker Desktop...'
            Get-Process 'Docker Desktop' -ErrorAction SilentlyContinue | Stop-Process -Force
            & wsl --shutdown
            Start-Process (DockerDesktopExe)
            if (-not (WaitForDocker 300)) {
                Say 'Docker Desktop did not come back. Open it, then double-click "Start Interpreter" again.'
                exit 1
            }
        }
    } elseif ($targetGb -ge 10) {
        Say "Warning: Docker can use only $dockerGb GB of memory (the interpreter needs about 10 GB)."
        Say "Add 'memory=${targetGb}GB' under [wsl2] in $wslConfig, then restart Docker Desktop."
    } else {
        Say "Warning: this PC has $pcGb GB of memory; the interpreter needs about 10 GB free for Docker."
        Say 'It may be slow or stop during a meeting.'
    }
}

# 3. Let phones on the Wi-Fi reach this PC (asks for administrator permission once).
$adapter = Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' } | Select-Object -First 1
$ip = $null
if ($adapter) { $ip = @($adapter.IPv4Address)[0].IPAddress }
if (-not (Get-NetFirewallRule -DisplayName $FirewallRule -ErrorAction SilentlyContinue)) {
    Say 'Phones need permission to reach this PC on the Wi-Fi.'
    if (Ask 'Allow it? (Windows will ask for administrator permission)') {
        $command = "New-NetFirewallRule -DisplayName '$FirewallRule' -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow -Profile Private,Domain"
        try {
            Start-Process powershell -Verb RunAs -Wait -ArgumentList '-NoProfile', '-Command', $command
        } catch {
            Say 'Skipped - phones may not be able to find this PC.'
        }
    }
}
if ($adapter) {
    $networkCategory = (Get-NetConnectionProfile -InterfaceIndex $adapter.InterfaceIndex -ErrorAction SilentlyContinue).NetworkCategory
    if ("$networkCategory" -eq 'Public') {
        Say 'Note: Windows treats this Wi-Fi as a Public network, which blocks phones from reaching this PC.'
        Say '      Settings -> Network & internet -> Wi-Fi -> this network -> Private network.'
    }
}
if ($ip) { $Env:INTERPRETER_PHONE_URL = "http://${ip}:$Port" }

# 4. Download (first time ~15 GB) and start
Say 'Getting the interpreter ready. The first time this downloads about 15 GB.'
& docker compose pull --ignore-pull-failures
& docker compose up -d
if ($LASTEXITCODE -ne 0) { Say 'Starting failed - see the messages above.'; exit 1 }

Say 'Starting up (the first start also downloads the 4.9 GB translation model)...'
$started = Get-Date
while ($true) {
    try {
        Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "http://localhost:$Port/healthz" | Out-Null
        break
    } catch { }
    # A one-shot setup step that failed, or the translator crashing in a loop.
    $failed = & docker compose ps -a --format '{{.Service}} {{.State}} {{.ExitCode}}' |
        Where-Object { ($_ -match '^(setup|attendee-setup|ollama-pull) exited (\d+)$' -and $Matches[2] -ne '0') -or $_ -match '^translator restarting' }
    if ($failed) {
        Say 'Something failed while starting. Details:'
        & docker compose logs --tail 40 setup attendee-setup ollama-pull translator
        exit 1
    }
    if (((Get-Date) - $started).TotalMinutes -gt 90) {
        Say 'Still not ready after 90 minutes. Check the internet connection, then try again.'
        exit 1
    }
    Start-Sleep -Seconds 10
    Write-Host -NoNewline '.'
}
Say ''

# The translator is up; the meeting service (Attendee) can take a little longer.
for ($i = 0; $i -lt 60; $i++) {
    try {
        $config = Invoke-RestMethod -TimeoutSec 10 "http://localhost:$Port/api/bots/config"
        if ($config.attendee_ready) { break }
    } catch { }
    Start-Sleep -Seconds 5
}

Say ''
Say 'Ready.'
Say "  On this PC:     http://localhost:$Port/bot.html"
Say '  On your phone:  Interpreter app -> Meeting Bot (it finds this PC by itself)'
if ($ip) { Say "                  or open http://${ip}:$Port/bot.html in the phone's browser" }
Say '  To stop it:     double-click "Stop Interpreter"'
if (-not $NoBrowser) { Start-Process "http://localhost:$Port/bot.html" }
