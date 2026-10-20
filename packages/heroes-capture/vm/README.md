# A Hyper-V VM for heroes-capture

heroes-capture works the game through the Windows desktop: it brings the game's window to the
front, presses its keys, moves its mouse and grabs the screen. Run in a VM, it does all that on
the VM's desktop, so the PC stays free to use. The VM's monitor can also be any size, 4K included,
whatever monitors the PC has.

`heroes-vm.cmd` sets one up with nothing asked along the way:

- Windows 11 from your ISO, signing in to a local account by itself, with no setup screens.
- A share of the PC's GPU (GPU-PV, what WSL2 and Windows Sandbox use), with the PC's GPU driver
  copied in.
- A virtual monitor ([Virtual Display Driver](https://github.com/VirtualDrivers/Virtual-Display-Driver))
  with one mode, the `Resolution` in [settings.psd1](settings.psd1) (3840x2160 to start with).
- The screen never turns off, sleeps or locks; no notifications draw over the game; Windows
  Update never restarts it while it's signed in.
- Battle.net, and `heroes-capture.exe` in `C:\heroes-capture`.

## What it needs

- Windows 10 or 11 **Pro**, Enterprise or Education on the PC (Home has no Hyper-V), with
  virtualization turned on in the BIOS. Windows 11 on both sides, the same version, works best.
- The GPU's current driver from NVIDIA, AMD or Intel, not Windows Update's.
- A Windows 11 ISO in your Downloads folder: on
  [Microsoft's download page](https://www.microsoft.com/software-download/windows11), _Download
  Windows 11 Disk Image (ISO) for x64 devices_ (not the Media Creation Tool).
- About 150 GB free where Hyper-V keeps virtual disks (`Folder` in the settings to put it
  elsewhere), 8 GB of memory and 4 cores to give it (`Memory`, `Processors`).
- A Windows product key for the VM (`ProductKey`). Without one Windows runs unactivated and draws
  an "Activate Windows" watermark over the screen, and so over the captures.

Your own values go in `settings.local.psd1` next to `settings.psd1` (not tracked), with just the
keys you change:

```powershell
@{
    ProductKey = 'XXXXX-XXXXX-XXXXX-XXXXX-XXXXX'
    Resolution = '3840x2160'
}
```

## Making it

Run `vm\heroes-vm.cmd` from your copy of heroes-capture. It asks for an administrator once.

- If Hyper-V is off it turns it on and stops: restart Windows and run it again.
- Then it takes 20 to 30 minutes and prints each step: the disk, Windows, the VM, the first
  start, then the setup inside the VM. It ends with `heroes-capture is ready at 3840x2160`.
- Its log is `heroes-vm.log` in the VM's folder. The VM's password is in `credential.xml` there,
  readable only by you. The last line of the run says how to show it.

## Signing in to Battle.net (once)

Open the VM: Hyper-V Manager, `heroes-capture`, **Connect**. If it offers a display size first,
that's an _enhanced session_, which is a Remote Desktop connection: close that window, turn off
**View > Enhanced session**, and connect again. The basic session shows the console, the desktop
heroes-capture works on. An enhanced session moves the signed-in desktop away from it.

Battle.net's sign-in is waiting on the screen:

1. Sign in with the account the VM should use. A second, free account keeps your own out of it:
   create one at [battle.net](https://account.battle.net/creation/flow/creation-full).
   Heroes is free to play.
2. Install Heroes of the Storm.
3. Start it, and set its display mode to **Windowed (Fullscreen)**.

## Checking it works

The VM has two monitors. Monitor 1 is Hyper-V's own, the one the Connect window shows. Monitor 2
is the virtual one at your resolution, which nothing on the PC shows. heroes-capture puts the game
there with `--monitor 2`. With the game running, in a Command Prompt in the VM:

```bat
cd C:\heroes-capture
heroes-capture map render "Dragon Shire" --probe-monitor --monitor 2
```

The log's first line lists the monitors, and `monitor probe at 3840x2160: n of 6 checks ok` sums
up the check. Its pictures are in `C:\heroes-capture\tmp\probe-monitor-...`.

## Living with it

- Close the Connect window whenever you like: the VM keeps running, signed in, and so does a
  render.
- The VM shares the GPU: games on the PC run slower while it renders.
- A VM with a GPU share can't be saved or checkpointed. When the PC shuts down, the VM shuts down
  with it. It doesn't start by itself: start it from Hyper-V Manager.
- **After updating the GPU driver on the PC**, restart the PC and run `heroes-vm.cmd` again. The
  VM needs the same driver files as the PC.
- **For another resolution**, change `Resolution` in `settings.local.psd1` and run
  `heroes-vm.cmd` again.

Run again on an existing VM, `heroes-vm.cmd` shuts it down, copies the GPU driver's files and the
monitor's settings in, and starts it again. If the setup inside it never finished, that runs again
too.

To remove it all: delete the VM in Hyper-V Manager, then its folder.

## Where it comes from

The GPU-PV part (the VM's settings, which driver files go where) follows
[Easy-GPU-PV](https://github.com/jamesstringerparsec/Easy-GPU-PV). The virtual monitor's driver
installs the way its own silent install script does it, with
[nefcon](https://github.com/nefarius/nefcon). Easy-GPU-PV's successor, App Sandbox, needs no
Hyper-V, but its Windows VMs have a fixed 1080p screen.
