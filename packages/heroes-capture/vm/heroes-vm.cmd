@echo off
rem Creates the heroes-capture VM, or refreshes an existing one (README.md). No arguments: the
rem settings are in settings.psd1 and settings.local.psd1. Asks for an administrator once.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0heroes-vm.ps1"
