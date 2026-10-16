<#
Creates the Hyper-V VM heroes-capture runs in, or brings an existing one up to date. heroes-vm.cmd
runs it; README.md says what it needs and what is left to do by hand.

A new VM: Windows 11 applied from the ISO straight onto a new disk, signing in by itself with no
setup screens; a share of this PC's GPU (GPU-PV) with the host's driver files copied in; a virtual
monitor at settings.psd1's Resolution; then guest\setup.ps1 inside it (power settings, the virtual
monitor's driver, Battle.net, heroes-capture).

An existing VM: shut down, its GPU driver files and monitor resolution refreshed (after a GPU driver
update on this PC, or a new Resolution), started again; guest\setup.ps1 runs again if it never
finished.

The way through GPU-PV (the VM's memory-mapped IO, the GPU partition's values, which driver files
go where) follows Easy-GPU-PV: https://github.com/jamesstringerparsec/Easy-GPU-PV
#>
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    # Hyper-V and mounting disks need an administrator: again, elevated, in a window that stays open.
    Start-Process powershell.exe -Verb RunAs -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-NoExit', '-File', "`"$PSCommandPath`""
    exit
}

$settings = Import-PowerShellDataFile (Join-Path $PSScriptRoot 'settings.psd1')
$localSettings = Join-Path $PSScriptRoot 'settings.local.psd1'
if (Test-Path $localSettings) {
    (Import-PowerShellDataFile $localSettings).GetEnumerator() | ForEach-Object { $settings[$_.Key] = $_.Value }
}
if ($settings.Resolution -notmatch '^(\d+)x(\d+)$') { throw "Resolution should look like 3840x2160, not '$($settings.Resolution)'." }
$width, $height = [int]$Matches[1], [int]$Matches[2]

$feature = Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All -ErrorAction SilentlyContinue
if (-not $feature) { throw 'Hyper-V is not available on this edition of Windows: it needs Windows 10 or 11 Pro, Enterprise or Education.' }
if ($feature.State -ne 'Enabled') {
    Write-Host 'Turning on Hyper-V ...'
    Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All -All -NoRestart | Out-Null
    Write-Host 'Hyper-V is on. Restart Windows, then run heroes-vm.cmd again.'
    exit 1
}
Import-Module Hyper-V

$name = $settings.VMName
$folder = if ($settings.Folder) { $settings.Folder } else { Join-Path (Get-VMHost).VirtualHardDiskPath $name }
New-Item -ItemType Directory $folder -Force | Out-Null
$vhdPath = Join-Path $folder "$name.vhdx"
$credentialPath = Join-Path $folder 'credential.xml'
$setupTask = 'heroes-vm setup'

$EspType = '{c12a7328-f81f-11d2-ba4b-00a0c93ec93b}'
$MsrType = '{e3c9e316-0b5c-4db8-817d-f92df00215ae}'
$DataType = '{ebd0a0a2-b9e5-4433-87c0-68b6b72699c7}'

function Get-SharedGpu {
    # Msvm_PartitionableGpu names a GPU by its device path: \\?\PCI#VEN_10DE&DEV_2684&...#4&...&0&0008#{guid}
    # is the device PCI\VEN_10DE&DEV_2684&...\4&...&0&0008.
    $found = @()
    foreach ($gpu in @(Get-CimInstance -Namespace root\virtualization\v2 -ClassName Msvm_PartitionableGpu)) {
        $instanceId = ($gpu.Name -replace '^\\\\\?\\', '' -replace '#\{[^}]+\}$', '') -replace '#', '\'
        $device = Get-PnpDevice -InstanceId $instanceId -ErrorAction SilentlyContinue
        if ($device -and $device.Status -eq 'OK') {
            $found += $device.FriendlyName
            if ($settings.Gpu -eq 'AUTO' -or $device.FriendlyName -eq $settings.Gpu) {
                return [pscustomobject]@{ Path = $gpu.Name; Device = $device }
            }
        }
    }
    if (-not $found) { throw 'No GPU in this PC can be shared with a VM (GPU-PV): it needs an NVIDIA, AMD or Intel GPU with a current driver from its maker.' }
    throw "Gpu '$($settings.Gpu)' can't be shared with a VM. These can: $($found -join ', ')."
}

function Find-Iso {
    if ($settings.Iso) {
        if (-not (Test-Path $settings.Iso)) { throw "Iso: no file at $($settings.Iso)." }
        return $settings.Iso
    }
    $downloads = (New-Object -ComObject Shell.Application).NameSpace('shell:Downloads').Self.Path
    $iso = Get-ChildItem $downloads -Filter 'Win11*.iso' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $iso) {
        throw "No Windows 11 ISO (Win11*.iso) in $downloads. Download one from https://www.microsoft.com/software-download/windows11 (Download Windows 11 Disk Image (ISO) for x64 devices), or set Iso in settings.local.psd1."
    }
    $iso.FullName
}

function New-Password {
    -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 20 | ForEach-Object { [char]$_ })
}

function New-Unattend($image, $password) {
    # Answers for Windows' first start: the computer name and locale in the specialize pass; the
    # local account, automatic sign-in and no setup screens in oobeSystem.
    $esc = { param($text) [Security.SecurityElement]::Escape([string]$text) }
    $component = 'processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS"'
    $locale = (Get-Culture).Name
    $keyboard = (Get-WinUserLanguageList)[0].InputMethodTips[0]
    $productKey = if ($settings.ProductKey) { "<ProductKey>$(& $esc $settings.ProductKey)</ProductKey>" } else { '' }
    $user = & $esc $settings.UserName
    $pass = & $esc $password
    @"
<?xml version="1.0" encoding="utf-8"?>
<unattend xmlns="urn:schemas-microsoft-com:unattend" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State">
  <settings pass="specialize">
    <component name="Microsoft-Windows-Shell-Setup" $component>
      <ComputerName>$(& $esc $settings.ComputerName)</ComputerName>
      <TimeZone>$(& $esc (Get-TimeZone).Id)</TimeZone>
      $productKey
    </component>
  </settings>
  <settings pass="oobeSystem">
    <component name="Microsoft-Windows-International-Core" $component>
      <InputLocale>$(& $esc $keyboard)</InputLocale>
      <SystemLocale>$locale</SystemLocale>
      <UILanguage>$(& $esc $image.Languages[0])</UILanguage>
      <UserLocale>$locale</UserLocale>
    </component>
    <component name="Microsoft-Windows-Shell-Setup" $component>
      <OOBE>
        <HideEULAPage>true</HideEULAPage>
        <HideOEMRegistrationScreen>true</HideOEMRegistrationScreen>
        <HideOnlineAccountScreens>true</HideOnlineAccountScreens>
        <HideWirelessSetupInOOBE>true</HideWirelessSetupInOOBE>
        <ProtectYourPC>3</ProtectYourPC>
      </OOBE>
      <UserAccounts>
        <LocalAccounts>
          <LocalAccount wcm:action="add">
            <Name>$user</Name>
            <Group>Administrators</Group>
            <Password><Value>$pass</Value><PlainText>true</PlainText></Password>
          </LocalAccount>
        </LocalAccounts>
      </UserAccounts>
      <AutoLogon>
        <Enabled>true</Enabled>
        <Username>$user</Username>
        <Password><Value>$pass</Value><PlainText>true</PlainText></Password>
        <LogonCount>1</LogonCount>
      </AutoLogon>
    </component>
  </settings>
</unattend>
"@
}

function Install-Windows($windowsRoot, $systemRoot, $password) {
    $iso = Find-Iso
    Write-Host "Installing $($settings.Edition) from $iso ..."
    $mounted = Mount-DiskImage -ImagePath $iso -PassThru
    try {
        $isoRoot = "$(($mounted | Get-Volume).DriveLetter):\"
        $wim = 'sources\install.wim', 'sources\install.esd' | ForEach-Object { Join-Path $isoRoot $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
        if (-not $wim) { throw "$iso has no sources\install.wim or install.esd: is it a Windows ISO?" }
        $images = Get-WindowsImage -ImagePath $wim
        $index = ($images | Where-Object ImageName -eq $settings.Edition).ImageIndex
        if (-not $index) { throw "$iso has no '$($settings.Edition)'. It has: $(($images.ImageName) -join ', ')." }
        $image = Get-WindowsImage -ImagePath $wim -Index $index
        $hostBuild = (Get-CimInstance Win32_OperatingSystem).BuildNumber
        if ($image.Version -notlike "10.0.$hostBuild.*") {
            Write-Warning "The ISO's Windows ($($image.Version)) is not this PC's (build $hostBuild); GPU-PV works best with the same version on both."
        }
        Write-Host '  copying Windows onto the disk (several minutes) ...'
        Expand-WindowsImage -ImagePath $wim -Index $index -ApplyPath $windowsRoot | Out-Null
        & (Join-Path $windowsRoot 'Windows\System32\bcdboot.exe') (Join-Path $windowsRoot 'Windows') /s $systemRoot.TrimEnd('\') /f UEFI | Out-Null
        if ($LASTEXITCODE) { throw "bcdboot failed (exit code $LASTEXITCODE)." }
        $panther = Join-Path $windowsRoot 'Windows\Panther'
        New-Item -ItemType Directory $panther -Force | Out-Null
        Set-Content (Join-Path $panther 'unattend.xml') (New-Unattend $image $password) -Encoding UTF8
    } finally {
        Dismount-DiskImage -ImagePath $iso | Out-Null
    }
}

function Copy-GpuDriverFiles($gpu, $windowsRoot) {
    # The guest's GPU driver is the host's: the driver store folders go to HostDriverStore, other
    # files (System32's nvapi64.dll and the like) to the same place in the guest.
    Write-Host "Copying the $($gpu.Device.FriendlyName) driver's files ..."
    $hostStore = Join-Path $windowsRoot 'Windows\System32\HostDriverStore'
    Remove-Item (Join-Path $hostStore 'FileRepository') -Recurse -Force -ErrorAction SilentlyContinue
    $repository = Join-Path $env:SystemRoot 'System32\DriverStore\FileRepository\'
    $copy = {
        param($path)
        $path = $path -replace '^\\SystemRoot', $env:SystemRoot -replace '^\\\?\?\\', ''
        if ($path.StartsWith($repository, [StringComparison]::OrdinalIgnoreCase)) {
            $package = $path.Substring($repository.Length).Split('\')[0]
            $destination = Join-Path $hostStore "FileRepository\$package"
            if (-not (Test-Path $destination)) { Copy-Item (Join-Path $repository $package) $destination -Recurse }
        } else {
            $destination = Join-Path $windowsRoot $path.Substring(3)
            New-Item -ItemType Directory (Split-Path $destination) -Force | Out-Null
            Copy-Item $path $destination -Force
        }
    }
    New-Item -ItemType Directory (Join-Path $hostStore 'FileRepository') -Force | Out-Null
    if ($gpu.Device.FriendlyName -like 'NVIDIA*') {
        New-Item -ItemType Directory (Join-Path $windowsRoot 'Windows\System32\drivers\Nvidia Corporation') -Force | Out-Null
    }
    & $copy (Get-CimInstance Win32_SystemDriver -Filter "Name='$($gpu.Device.Service)'").PathName
    # The driver's files: Win32_PNPSignedDriverCIMDataFile pairs a device (Antecedent, by its
    # DeviceID) with each file (Dependent, CIM_DataFile.Name="c:\\windows\\...").
    $device = 'DeviceID="' + $gpu.Device.InstanceId.Replace('\', '\\') + '"'
    Get-WmiObject Win32_PNPSignedDriverCIMDataFile |
        Where-Object { $_.Antecedent.EndsWith($device, [StringComparison]::OrdinalIgnoreCase) } |
        ForEach-Object { & $copy ($_.Dependent.Split('=', 2)[1].Trim('"') -replace '\\\\', '\') }
}

function Write-DisplaySettings($gpu, $windowsRoot) {
    # The Virtual Display Driver's settings (C:\VirtualDisplayDriver\vdd_settings.xml, read when it
    # starts): one monitor, drawn by the shared GPU, with one mode.
    $folder = Join-Path $windowsRoot 'VirtualDisplayDriver'
    New-Item -ItemType Directory $folder -Force | Out-Null
    $gpuName = [Security.SecurityElement]::Escape($gpu.Device.FriendlyName)
    Set-Content (Join-Path $folder 'vdd_settings.xml') -Encoding UTF8 @"
<?xml version='1.0' encoding='utf-8'?>
<vdd_settings>
    <monitors>
        <count>1</count>
    </monitors>
    <gpu>
        <friendlyname>$gpuName</friendlyname>
    </gpu>
    <global>
        <g_refresh_rate>60</g_refresh_rate>
    </global>
    <resolutions>
        <resolution>
            <width>$width</width>
            <height>$height</height>
            <refresh_rate>60</refresh_rate>
        </resolution>
    </resolutions>
    <options>
        <CustomEdid>false</CustomEdid>
        <PreventSpoof>false</PreventSpoof>
        <EdidCeaOverride>false</EdidCeaOverride>
        <HardwareCursor>true</HardwareCursor>
        <SDR10bit>false</SDR10bit>
        <HDRPlus>false</HDRPlus>
        <logging>false</logging>
        <debuglogging>false</debuglogging>
    </options>
</vdd_settings>
"@
}

function New-VmDisk {
    Write-Host "Creating the disk $vhdPath ..."
    New-VHD -Path $vhdPath -SizeBytes $settings.DiskSize -Dynamic | Out-Null
    $disk = Mount-VHD -Path $vhdPath -PassThru | Get-Disk
    Initialize-Disk -Number $disk.Number -PartitionStyle GPT
    Get-Partition -DiskNumber $disk.Number -ErrorAction SilentlyContinue | Remove-Partition -Confirm:$false
    # The EFI system partition starts as a data partition so it can have a drive letter for
    # bcdboot; it gets its real type once the boot files are on it.
    $system = New-Partition -DiskNumber $disk.Number -Size 260MB -GptType $DataType
    $system | Format-Volume -FileSystem FAT32 -NewFileSystemLabel System -Force -Confirm:$false | Out-Null
    $system | Add-PartitionAccessPath -AssignDriveLetter
    New-Partition -DiskNumber $disk.Number -Size 16MB -GptType $MsrType | Out-Null
    $windows = New-Partition -DiskNumber $disk.Number -UseMaximumSize -GptType $DataType
    $windows | Format-Volume -FileSystem NTFS -NewFileSystemLabel Windows -Force -Confirm:$false | Out-Null
    $windows | Add-PartitionAccessPath -AssignDriveLetter
    [pscustomobject]@{
        Disk    = $disk.Number
        System  = (Get-Partition -DiskNumber $disk.Number -PartitionNumber $system.PartitionNumber)
        Windows = (Get-Partition -DiskNumber $disk.Number -PartitionNumber $windows.PartitionNumber)
    }
}

function Open-VmDisk {
    # The existing disk's Windows partition (its largest), with a drive letter.
    $disk = Mount-VHD -Path $vhdPath -PassThru | Get-Disk
    $windows = Get-Partition -DiskNumber $disk.Number | Sort-Object Size -Descending | Select-Object -First 1
    if (-not [char]::IsLetter($windows.DriveLetter)) {
        $windows | Add-PartitionAccessPath -AssignDriveLetter
        $windows = Get-Partition -DiskNumber $disk.Number -PartitionNumber $windows.PartitionNumber
    }
    "$($windows.DriveLetter):\"
}

function Update-Guest($gpu, $windowsRoot) {
    Copy-GpuDriverFiles $gpu $windowsRoot
    Write-DisplaySettings $gpu $windowsRoot
    $payload = Join-Path $windowsRoot 'HeroesVM'
    New-Item -ItemType Directory $payload -Force | Out-Null
    Copy-Item (Join-Path $PSScriptRoot 'guest\setup.ps1') $payload -Force
}

function New-CaptureVM($gpu) {
    Write-Host "Creating the VM $name ..."
    if (-not (Get-VMSwitch -Name 'Default Switch' -ErrorAction SilentlyContinue)) { throw "Hyper-V's Default Switch is missing; the VM uses it for its internet connection." }
    New-VM -Name $name -Generation 2 -MemoryStartupBytes $settings.Memory -VHDPath $vhdPath -SwitchName 'Default Switch' -Path $folder | Out-Null
    # GPU-PV VMs can't be checkpointed or saved; the GPU needs the large memory-mapped IO spaces.
    Set-VM -Name $name -ProcessorCount $settings.Processors -StaticMemory -CheckpointType Disabled -AutomaticCheckpointsEnabled $false `
        -LowMemoryMappedIoSpace 3GB -HighMemoryMappedIoSpace 32GB -GuestControlledCacheTypes $true -AutomaticStartAction Nothing -AutomaticStopAction ShutDown
    Set-VMKeyProtector -VMName $name -NewLocalKeyProtector
    Enable-VMTPM -VMName $name
    if ((Get-Command Add-VMGpuPartitionAdapter).Parameters.ContainsKey('InstancePath')) {
        Add-VMGpuPartitionAdapter -VMName $name -InstancePath $gpu.Path
    } else {
        Add-VMGpuPartitionAdapter -VMName $name   # Windows 10 hosts: the first GPU
    }
    $share = [decimal]$settings.GpuShare / 100
    $units = [uint64]([decimal]1000000000 * $share)
    $encode = [uint64]([decimal][uint64]::MaxValue * $share)
    Set-VMGpuPartitionAdapter -VMName $name -MinPartitionVRAM $units -MaxPartitionVRAM $units -OptimalPartitionVRAM $units `
        -MinPartitionEncode $encode -MaxPartitionEncode $encode -OptimalPartitionEncode $encode `
        -MinPartitionDecode $units -MaxPartitionDecode $units -OptimalPartitionDecode $units `
        -MinPartitionCompute $units -MaxPartitionCompute $units -OptimalPartitionCompute $units
}

function Wait-SignedIn($credential) {
    $deadline = (Get-Date).AddMinutes(30)
    while ($true) {
        try {
            if (Invoke-Command -VMName $name -Credential $credential -ErrorAction Stop { [bool](Get-Process explorer -ErrorAction SilentlyContinue) }) { return }
        } catch { }
        if ((Get-Date) -gt $deadline) { throw "The VM hasn't signed in after 30 minutes; open it in Hyper-V Manager to see where it stopped." }
        Start-Sleep 10
    }
}

function Set-AutoSignIn($credential) {
    # Windows' first sign-in used up the answer file's one automatic sign-in; from now on, always.
    Invoke-Command -VMName $name -Credential $credential -ArgumentList $credential.UserName, $credential.GetNetworkCredential().Password {
        param($user, $password)
        $winlogon = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
        Set-ItemProperty $winlogon AutoAdminLogon '1'
        Set-ItemProperty $winlogon DefaultUserName $user
        Set-ItemProperty $winlogon DefaultPassword $password
        Set-ItemProperty $winlogon DefaultDomainName $env:COMPUTERNAME
        Remove-ItemProperty $winlogon AutoLogonCount -ErrorAction SilentlyContinue
    }
}

function Invoke-GuestSetup($credential) {
    if (Invoke-Command -VMName $name -Credential $credential { Test-Path C:\HeroesVM\setup.done }) { return }
    Write-Host 'Setting up inside the VM:'
    # A scheduled task runs it in the signed-in session (PowerShell Direct's own session has no desktop).
    Invoke-Command -VMName $name -Credential $credential -ArgumentList $credential.UserName, $setupTask {
        param($user, $task)
        Remove-Item C:\HeroesVM\setup.log, C:\HeroesVM\setup.failed -ErrorAction SilentlyContinue
        $action = New-ScheduledTaskAction -Execute powershell.exe -Argument '-NoProfile -ExecutionPolicy Bypass -File C:\HeroesVM\setup.ps1'
        $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
        Register-ScheduledTask -TaskName $task -Action $action -Principal $principal -Force | Out-Null
        Start-ScheduledTask -TaskName $task
    }
    $shown = 0
    $deadline = (Get-Date).AddMinutes(60)
    do {
        Start-Sleep 5
        $state = Invoke-Command -VMName $name -Credential $credential -ArgumentList $setupTask {
            param($task)
            [pscustomobject]@{
                Log      = @(Get-Content C:\HeroesVM\setup.log -ErrorAction SilentlyContinue)
                Done     = Test-Path C:\HeroesVM\setup.done
                Failed   = Test-Path C:\HeroesVM\setup.failed
                # 267011: the task has not run yet.
                Finished = (Get-ScheduledTask $task).State -eq 'Ready' -and (Get-ScheduledTaskInfo $task).LastTaskResult -ne 267011
            }
        }
        $log = @($state.Log)
        $log | Select-Object -Skip $shown | ForEach-Object { Write-Host "  $_" }
        $shown = $log.Count
        if ((Get-Date) -gt $deadline) { throw 'The setup inside the VM is still running after an hour; see C:\HeroesVM\setup.log in the VM.' }
    } until ($state.Done -or $state.Failed -or $state.Finished)
    Invoke-Command -VMName $name -Credential $credential -ArgumentList $setupTask { param($task) Unregister-ScheduledTask $task -Confirm:$false }
    if (-not $state.Done) { throw 'The setup inside the VM failed (its log is above, and in C:\HeroesVM\setup.log in the VM). Run heroes-vm.cmd again to retry it.' }
}

Start-Transcript (Join-Path $folder 'heroes-vm.log') | Out-Null
try {
    $gpu = Get-SharedGpu
    $vm = Get-VM -Name $name -ErrorAction SilentlyContinue
    if ($vm) {
        if (-not (Test-Path $credentialPath)) { throw "$credentialPath is missing, so the VM can't be signed in to. Delete the VM (and $folder) to start again." }
        $credential = Import-Clixml $credentialPath
        if ($vm.State -ne 'Off') {
            Write-Host "Shutting $name down to refresh it ..."
            Stop-VM -Name $name -Force   # a shutdown from inside Windows, without asking
        }
        try { Update-Guest $gpu (Open-VmDisk) } finally { Dismount-VHD -Path $vhdPath }
    } else {
        if (Test-Path $vhdPath) { throw "$vhdPath is left from an earlier attempt; delete it and run this again." }
        $password = New-Password
        $credential = New-Object PSCredential($settings.UserName, (ConvertTo-SecureString $password -AsPlainText -Force))
        try {
            $disk = New-VmDisk
            $systemRoot = "$($disk.System.DriveLetter):\"
            $windowsRoot = "$($disk.Windows.DriveLetter):\"
            Install-Windows $windowsRoot $systemRoot $password
            Update-Guest $gpu $windowsRoot
            Remove-PartitionAccessPath -DiskNumber $disk.Disk -PartitionNumber $disk.System.PartitionNumber -AccessPath $systemRoot
            Set-Partition -DiskNumber $disk.Disk -PartitionNumber $disk.System.PartitionNumber -GptType $EspType
            Dismount-VHD -Path $vhdPath
        } catch {
            # A half-made disk is no use to a later run.
            Dismount-VHD -Path $vhdPath -ErrorAction SilentlyContinue
            Remove-Item $vhdPath -ErrorAction SilentlyContinue
            throw
        }
        $credential | Export-Clixml $credentialPath
        New-CaptureVM $gpu
    }
    Write-Host "Starting $name; waiting for it to sign in (the first start installs Windows: 5 to 10 minutes) ..."
    Start-VM -Name $name
    Wait-SignedIn $credential
    Set-AutoSignIn $credential
    Invoke-GuestSetup $credential
    Write-Host ''
    Write-Host "$name is ready at $($settings.Resolution). Open it from Hyper-V Manager (Connect); its password: (Import-Clixml '$credentialPath').GetNetworkCredential().Password"
} catch {
    Write-Host ''
    Write-Host "Failed: $_" -ForegroundColor Red
} finally {
    Stop-Transcript | Out-Null
}
