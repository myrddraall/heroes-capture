<#
Sets the heroes-capture VM up from inside: the session stays signed in with the screen on and no
notifications over it, the virtual monitor's driver goes in, and Battle.net and heroes-capture.exe
are installed. heroes-vm.ps1 copies it to C:\HeroesVM and runs it as a scheduled task in the
signed-in session (with an administrator's rights), and shows its log as it goes. Done once:
C:\HeroesVM\setup.done marks it finished.
#>
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest is very slow while it draws its progress

$here = 'C:\HeroesVM'
$log = Join-Path $here 'setup.log'
$downloads = Join-Path $here 'downloads'

# The Virtual Display Driver release (its driver only, signed; the x86-named zip holds the amd64
# driver) and the nefcon release its own silent install script uses.
$VddUrl = 'https://github.com/VirtualDrivers/Virtual-Display-Driver/releases/download/25.7.23/VirtualDisplayDriver-x86.Driver.Only.zip'
$NefconUrl = 'https://github.com/nefarius/nefcon/releases/download/v1.14.0/nefcon_v1.14.0.zip'
$VcRedistUrl = 'https://aka.ms/vs/17/release/vc_redist.x64.exe'
$BattleNetUrl = 'https://downloader.battle.net/download/getInstallerForGame?os=win&gameProgram=BATTLENET_APP&version=Live'
$HeroesCaptureUrl = 'https://github.com/myrddraall/heroes-capture/releases/latest/download/heroes-capture-win-x64.exe'

function Say($text) {
    Add-Content $log $text
    Write-Host $text
}

function Get-File($url, $file) {
    $path = Join-Path $downloads $file
    Invoke-WebRequest $url -OutFile $path -UseBasicParsing
    $path
}

function Wait-Until($what, $minutes, [scriptblock]$test) {
    $deadline = (Get-Date).AddMinutes($minutes)
    while (-not (& $test)) {
        if ((Get-Date) -gt $deadline) { throw "$what didn't happen within $minutes minutes." }
        Start-Sleep 5
    }
}

try {
    New-Item -ItemType Directory $downloads -Force | Out-Null

    $adapters = @(Get-CimInstance Win32_VideoController)
    Say "Display adapters: $(($adapters | ForEach-Object { "$($_.Name) ($($_.Status))" }) -join ', ')"
    if (-not ($adapters | Where-Object { $_.Name -notmatch 'Hyper-V|Basic|Virtual Display' -and $_.Status -eq 'OK' })) {
        throw "The shared GPU isn't working in the VM. Update the GPU driver on the PC from its maker's site, restart, and run heroes-vm.cmd again."
    }

    Say 'Keeping the session signed in, the screen on, and notifications off ...'
    powercfg /change monitor-timeout-ac 0
    powercfg /change standby-timeout-ac 0
    powercfg /change hibernate-timeout-ac 0
    powercfg /hibernate off
    Set-ItemProperty 'HKCU:\Control Panel\Desktop' ScreenSaveActive '0'
    $push = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\PushNotifications'
    New-Item $push -Force | Out-Null
    New-ItemProperty $push ToastEnabled -Value 0 -PropertyType DWord -Force | Out-Null
    # Updates install, but don't restart Windows while it's signed in.
    $updates = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU'
    New-Item $updates -Force | Out-Null
    New-ItemProperty $updates NoAutoRebootWithLoggedOnUsers -Value 1 -PropertyType DWord -Force | Out-Null

    Say 'Waiting for the internet connection ...'
    Wait-Until 'A connection to github.com' 5 { Test-NetConnection github.com -Port 443 -InformationLevel Quiet -WarningAction SilentlyContinue }

    if (-not (Get-PnpDevice -PresentOnly | Where-Object { $_.HardwareID -contains 'Root\MttVDD' })) {
        Say 'Installing the virtual monitor (Virtual Display Driver) ...'
        $vcRedist = Get-File $VcRedistUrl 'vc_redist.x64.exe'
        Start-Process $vcRedist '/install /quiet /norestart' -Wait
        $vdd = Join-Path $downloads 'vdd'
        Expand-Archive (Get-File $VddUrl 'vdd.zip') $vdd -Force
        $nefcon = Join-Path $downloads 'nefcon'
        Expand-Archive (Get-File $NefconUrl 'nefcon.zip') $nefcon -Force
        # The driver's signing certificates, trusted first so its install doesn't stop to ask.
        $certificates = New-Object Security.Cryptography.X509Certificates.X509Certificate2Collection
        $certificates.Import([IO.File]::ReadAllBytes((Join-Path $vdd 'VirtualDisplayDriver\mttvdd.cat')))
        $store = New-Object Security.Cryptography.X509Certificates.X509Store('TrustedPublisher', 'LocalMachine')
        $store.Open('ReadWrite')
        $store.AddRange($certificates)
        $store.Close()
        & (Join-Path $nefcon 'x64\nefconc.exe') install (Join-Path $vdd 'VirtualDisplayDriver\MttVDD.inf') 'Root\MttVDD' | Out-Null
        if ($LASTEXITCODE -notin 0, 3010) { throw "Installing the Virtual Display Driver failed (nefcon exit code $LASTEXITCODE)." }
    }
    Wait-Until 'The virtual monitor starting' 2 { Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match 'Virtual Display' -and $_.Status -eq 'OK' } }
    $monitor = Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match 'Virtual Display' }
    Say "Virtual monitor: $($monitor.CurrentHorizontalResolution)x$($monitor.CurrentVerticalResolution)"

    $battleNet = Join-Path ${env:ProgramFiles(x86)} 'Battle.net'
    if (-not (Test-Path (Join-Path $battleNet 'Battle.net.exe'))) {
        Say 'Installing Battle.net ...'
        New-Item -ItemType Directory $battleNet -Force | Out-Null
        Start-Process (Get-File $BattleNetUrl 'Battle.net-Setup.exe') '--lang=enUS', "--installpath=`"$battleNet`""
        Wait-Until 'Battle.net installing' 15 { Test-Path (Join-Path $battleNet 'Battle.net.exe') }
    }

    Say 'Downloading heroes-capture.exe to C:\heroes-capture ...'
    New-Item -ItemType Directory C:\heroes-capture -Force | Out-Null
    Invoke-WebRequest $HeroesCaptureUrl -OutFile C:\heroes-capture\heroes-capture.exe -UseBasicParsing

    New-Item (Join-Path $here 'setup.done') -Force | Out-Null
    Say 'Done.'
} catch {
    Say "Failed: $_"
    Set-Content (Join-Path $here 'setup.failed') "$_"
}
