@echo off
rem What to run right now. Kept current with whatever the next test or render needs, so
rem update.cmd (which refreshes the files and then calls this) needs no arguments.
rem
rem Current step: map list, with tmp\ kept so its log comes back (pip, when it runs, writes
rem tmp\setup.log).
setlocal
pushd "%~dp0"
set "PYTHONPATH=%~dp0src"
rem The tool's dependencies, installed quietly when missing (pip's output in tmp\setup.log).
py -c "import heroes_capture.cli, numpy, PIL, pyvips, scipy, mss, pydirectinput, dxcam, typer, rich" 2>nul || call :install || goto :copy
py -m heroes_capture map list --keep-tmp
if errorlevel 1 echo.& echo Stopped: the step above failed.
popd

:copy
rem Copy this run's output (maps\ and tmp\, without the prepared map files) to the results
rem folder, which update.cmd points at the development machine's tmp\ folder.
if not defined HRS_RESULTS (
  echo.
  echo Results not copied back: HRS_RESULTS is not set. Run this through update.cmd.
  exit /b 0
)
if not exist "%~dp0tmp" if not exist "%~dp0maps" (
  echo.
  echo Nothing to copy back: the run left no tmp or maps folder.
  exit /b 1
)
echo.
echo Copying results to %HRS_RESULTS% ...
set "FAILED="
rem /XX: don't list the files already in the results folder that this run didn't make.
for %%F in (tmp maps) do if exist "%~dp0%%F" (
  robocopy "%~dp0%%F" "%HRS_RESULTS%\%%F" /E /XX /XF *.stormmap /NFL /NDL /NJH /NP
  if errorlevel 8 set "FAILED=1"
)
if defined FAILED (
  echo Copy FAILED - see the robocopy output above.
  exit /b 1
)
echo Results copied.
exit /b 0

:install
echo Installing heroes-capture's dependencies ...
if not exist "%~dp0tmp" mkdir "%~dp0tmp"
py -m pip install --quiet --no-warn-script-location --disable-pip-version-check -e . > "%~dp0tmp\setup.log" 2>&1
if errorlevel 1 (
  echo Installing failed; see tmp\setup.log.
  exit /b 1
)
exit /b 0
