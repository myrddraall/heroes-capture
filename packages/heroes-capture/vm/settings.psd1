# The heroes-capture VM's settings (heroes-vm.cmd reads them). To change one, put it in
# settings.local.psd1 next to this file (not tracked), as @{ Resolution = '2560x1440' } and so on.
@{
    # The VM's name in Hyper-V, and the computer name inside it (15 characters at most).
    VMName       = 'heroes-capture'
    ComputerName = 'HEROES-VM'

    # The Windows 11 ISO ("Download Windows 11 Disk Image (ISO) for x64 devices" on
    # https://www.microsoft.com/software-download/windows11). Empty: the newest Win11*.iso in
    # your Downloads folder.
    Iso          = ''
    Edition      = 'Windows 11 Pro'
    # A Windows product key for the VM. Without one Windows isn't activated, and an unactivated
    # Windows draws an "Activate Windows" watermark over the screen, captures included.
    ProductKey   = ''

    # The account the VM signs in to by itself. Its password is generated, and kept (encrypted for
    # you) in credential.xml in the VM's folder.
    UserName     = 'capture'

    # Where the VM's disk and files go. Empty: Hyper-V's folder for virtual disks.
    Folder       = ''
    DiskSize     = 128GB
    Memory       = 8GB
    Processors   = 4

    # The GPU to share with the VM (GPU-PV), by its name in Device Manager. AUTO: the first one
    # that can be shared.
    Gpu          = 'AUTO'
    # How much of it, in percent.
    GpuShare     = 50

    # The VM's game monitor: a virtual display with this one mode, at 60 Hz.
    Resolution   = '3840x2160'
}
