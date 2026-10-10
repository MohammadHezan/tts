# Starts the Meeting Interpreter on Windows (run by "Start Interpreter.bat").
# Windows PowerShell 5.1 compatible - no PowerShell 7 syntax.
#
# Starts Docker Desktop if needed, gives Docker enough memory, lets phones on
# the Wi-Fi reach this PC, then runs docker-compose.yml and opens the page.
param([switch]$NoBrowser)

$ErrorActionPreference = 'Stop'
# The folder with docker-compose.yml: this script's parent (deploy\..). In the
# Windows download that is the "app" folder next to "Start Interpreter".
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
function SayDockerHelp {
    Say ''
    Say "Docker's engine did not start. Try these, in order:"
    Say '  1. Open Docker Desktop. If it shows an agreement, click Accept (signing in is optional - skip it).'
    Say '     Wait until the bottom-left corner says "Engine running".'
    Say '  2. Restart the PC (needed once after installing Docker Desktop), then double-click "Start Interpreter" again.'
    Say '  3. Still stuck on "Engine starting"? Open PowerShell as administrator, run:  wsl --update'
    Say '     then restart the PC.'
    Say '  4. Task Manager -> Performance -> CPU: "Virtualization" must say Enabled. If it says Disabled,'
    Say '     turn it on in the PC''s BIOS/UEFI settings (called "SVM" on AMD, "VT-x" or "Intel Virtualization" on Intel).'
}
if (-not (DockerRunning)) {
    if (Test-Path (DockerDesktopExe)) {
        Say 'Starting Docker Desktop...'
        Start-Process (DockerDesktopExe)
    }
    Say "Waiting for Docker's engine (up to 5 minutes)..."
    if (-not (WaitForDocker 300)) {
        SayDockerHelp
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
                SayDockerHelp
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

# 3. An NVIDIA graphics card makes it several times faster and hear Arabic far
#    better (docker-compose.gpu.yml). Used only if Docker can actually reach it.
$useGpu = $false
if ($Env:INTERPRETER_CPU) {
    $gpuStatus = 'turned off with INTERPRETER_CPU'
} elseif (-not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
    $gpuStatus = 'no NVIDIA driver found on this PC (nvidia-smi is missing)'
} else {
    Say 'Checking the NVIDIA graphics card (the first time this also downloads part of the interpreter)...'
    $ErrorActionPreference = 'Continue'
    $gpuOutput = @(& docker run --rm --gpus all --entrypoint nvidia-smi ollama/ollama 2>&1 | ForEach-Object { "$_" })
    $useGpu = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = 'Stop'
    if ($useGpu) {
        $gpuName = (& nvidia-smi --query-gpu=name --format=csv,noheader 2>$null | Select-Object -First 1)
        $gpuStatus = "used: $gpuName"
    } else {
        $lastLine = ($gpuOutput | Where-Object { $_ -match '\S' } | Select-Object -Last 1)
        $gpuStatus = "Docker could not use it: $lastLine"
    }
}
# Shown on the interpreter's page, so a photo of it says what happened.
$Env:INTERPRETER_GPU_STATUS = $gpuStatus
if ($useGpu) {
    $Env:COMPOSE_FILE = 'docker-compose.yml;docker-compose.gpu.yml'
    Say "Using the NVIDIA graphics card - fast mode ($gpuStatus)."
} else {
    Remove-Item Env:COMPOSE_FILE -ErrorAction SilentlyContinue
    Say "Running on the processor, about 10-15 seconds per sentence. Graphics card: $gpuStatus"
}

# 4. Let phones on the Wi-Fi reach this PC (asks for administrator permission once).
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

# 4b. The dashboard login. It lives in .env (kept out of the download and out of git):
#     asked for once, and only a salted hash of the password is stored.
function NewPasswordHash([string]$Password) {
    $salt = New-Object byte[] 16
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($salt)
    $rng.Dispose()
    $kdf = New-Object Security.Cryptography.Rfc2898DeriveBytes($Password, $salt, 600000, [Security.Cryptography.HashAlgorithmName]::SHA256)
    $hash = $kdf.GetBytes(32)
    $kdf.Dispose()
    return 'pbkdf2_sha256:600000:' + [Convert]::ToBase64String($salt) + ':' + [Convert]::ToBase64String($hash)
}
function PlainText([Security.SecureString]$Secret) {
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secret)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}
$envFile = Join-Path (Get-Location) '.env'
$haveLogin = (Test-Path $envFile) -and (Select-String -Path $envFile -Pattern '^\s*INTERPRETER_ADMIN_PASSWORD_HASH=.+' -Quiet)
if (-not $haveLogin) {
    Say 'The dashboard needs a login, so that only you can send the bot into a meeting.'
    $user = (Read-Host 'Choose a username [admin]').Trim() -replace "'", ''
    if (-not $user) { $user = 'admin' }
    while ($true) {
        $first = PlainText (Read-Host 'Choose a password (at least 8 characters)' -AsSecureString)
        $again = PlainText (Read-Host 'Type it again' -AsSecureString)
        if ($first.Length -ge 8 -and $first -ceq $again) { break }
        Say 'The two must match and be at least 8 characters. Try again.'
    }
    $kept = @()
    if (Test-Path $envFile) { $kept = @(Get-Content $envFile | Where-Object { $_ -notmatch '^\s*INTERPRETER_ADMIN_(USER|PASSWORD_HASH)=' }) }
    $lines = $kept + "INTERPRETER_ADMIN_USER='$user'" + ("INTERPRETER_ADMIN_PASSWORD_HASH='" + (NewPasswordHash $first) + "'")
    [IO.File]::WriteAllText($envFile, (($lines -join "`n") + "`n"), (New-Object Text.UTF8Encoding $false))
    Say "Login saved (username: $user). To change it later, delete the .env file and start again."
}

# 4c. Our models (about 6 GB: the speech model and our trained translation model) are not in the
#     download itself. Fetched once from the GitHub release named in deploy\models.json, each part checked
#     against its checksum; an interrupted download resumes. Without them the interpreter still runs, with
#     Whisper for speech and the plain translation model (less accurate in Arabic).
$manifestFile = Join-Path (Get-Location) 'deploy\models.json'
$modelsDir = Join-Path (Get-Location) 'local-models'
if (-not $Env:INTERPRETER_SKIP_MODELS -and -not (Test-Path (Join-Path $modelsDir 'Modelfile')) -and (Test-Path $manifestFile)) {
    try {
        $manifest = Get-Content $manifestFile -Raw | ConvertFrom-Json
        $gb = [math]::Round($manifest.total_bytes / 1GB, 1)
        Say "Downloading the interpreter's models ($gb GB). This happens once; if it is interrupted, start again and it continues."
        $tmp = Join-Path (Get-Location) 'models-download'
        New-Item -ItemType Directory -Force $tmp | Out-Null
        $joined = Join-Path $tmp 'local-models.zip'
        if (Test-Path $joined) { Remove-Item $joined -Force }
        $out = [IO.File]::Create($joined)
        try {
            foreach ($part in $manifest.parts) {
                $file = Join-Path $tmp $part.name
                $good = { (Test-Path $file) -and ((Get-Item $file).Length -eq $part.bytes) -and ((Get-FileHash $file -Algorithm SHA256).Hash -eq $part.sha256.ToUpper()) }
                for ($attempt = 1; $attempt -le 5 -and -not (& $good); $attempt++) {
                    Say "  $($part.name) (attempt $attempt)"
                    & "$Env:SystemRoot\System32\curl.exe" -L -C - --fail --retry 3 --retry-delay 5 -o $file "$($manifest.release)/$($part.name)"
                    if (-not (& $good) -and (Test-Path $file) -and ((Get-Item $file).Length -ge $part.bytes)) { Remove-Item $file -Force }  # whole but wrong: start this part over
                }
                if (-not (& $good)) { throw "$($part.name) could not be downloaded intact" }
                $in = [IO.File]::OpenRead($file)
                try { $in.CopyTo($out) } finally { $in.Dispose() }
                Remove-Item $file -Force  # the part is in the joined file now: keeps the disk use down
            }
        } finally { $out.Dispose() }
        Say 'Unpacking the models...'
        New-Item -ItemType Directory -Force $modelsDir | Out-Null
        & "$Env:SystemRoot\System32\tar.exe" -xf $joined -C $modelsDir
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path (Join-Path $modelsDir 'Modelfile'))) { throw 'unpacking the models failed' }
        Remove-Item $tmp -Recurse -Force
        Say 'Models ready.'
    } catch {
        Say "Could not get the models: $($_.Exception.Message)"
        Say 'Continuing without them (Whisper for speech, the plain translation model). Start again later to retry.'
        if (Test-Path $modelsDir) { if (-not (Test-Path (Join-Path $modelsDir 'Modelfile'))) { Remove-Item $modelsDir -Recurse -Force -ErrorAction SilentlyContinue } }
    }
}

# 5. Download (first time ~15 GB) and start
Say 'Getting the interpreter ready. The first time this downloads about 15 GB.'
& docker compose pull --ignore-pull-failures
if ((Test-Path (Join-Path (Get-Location) 'local-models')) -and (Test-Path (Join-Path (Get-Location) '.git'))) {
    # A developer's checkout: our own build (the meeting-chat captions and the dashboard switches live in this folder's code).
    Say 'Building the translator from this folder (the first time takes a few minutes)...'
    & docker compose build translator
    if ($LASTEXITCODE -ne 0) { Say 'Building failed - see the messages above.'; exit 1 }
}
& docker compose up -d
if ($LASTEXITCODE -ne 0) { Say 'Starting failed - see the messages above.'; exit 1 }

$models = 'the 2.5 GB translation model'
$pulls = @('setup', 'attendee-setup', 'ollama-pull')
if ($useGpu) {  # docker-compose.gpu.yml: Gemma 3 4B, and the speech model
    $models = 'the translation model and the 4 GB speech model'
    $pulls += 'asr-pull'
}
Say "Starting up (the first start also downloads $models)..."
$started = Get-Date
while ($true) {
    try {
        Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "http://localhost:$Port/healthz" | Out-Null
        break
    } catch { }
    # A one-shot setup step that failed, or the translator crashing in a loop.
    $failed = & docker compose ps -a --format '{{.Service}} {{.State}} {{.ExitCode}}' |
        Where-Object { ($_ -match '^(setup|attendee-setup|ollama-pull|asr-pull) exited (\d+)$' -and $Matches[2] -ne '0') -or $_ -match '^translator restarting' }
    if ($failed) {
        Say 'Something failed while starting. Details:'
        & docker compose logs --tail 40 @pulls translator
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
        $config = Invoke-RestMethod -TimeoutSec 10 "http://localhost:$Port/api/ready"
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
