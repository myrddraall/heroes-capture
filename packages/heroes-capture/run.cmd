@echo off
rem What to run right now. Kept current with whatever the next test or render needs, so
rem update.cmd (which refreshes the files and then calls this) needs no arguments.
rem
rem Current step: the first run of the heroes-capture command (the tool is now a Python package,
rem src\heroes_capture; render.cmd is gone): Battlefield of Eternity, as before. The screen size
rem now comes from the primary monitor.
setlocal
pushd "%~dp0"
set "PYTHONPATH=%~dp0src"
py -c "import heroes_capture.cli, numpy, PIL, pyvips, scipy, mss, pydirectinput, dxcam" 2>nul || py -m pip install -e . || goto :copy
py -m heroes_capture map "Battlefield of Eternity"
if errorlevel 1 echo.& echo Stopped: the step above failed.
popd

:copy
rem Copy this run's output (everything in work\ except the map files) to the results folder,
rem which update.cmd points at the development machine's tmp\ folder.
if not defined HRS_RESULTS (
  echo.
  echo Results not copied back: HRS_RESULTS is not set. Run this through update.cmd.
  exit /b 0
)
if not exist "%~dp0work" (
  echo.
  echo Nothing to copy back: the run made no work folder.
  exit /b 1
)
echo.
echo Copying results to %HRS_RESULTS% ...
if not exist "%HRS_RESULTS%" mkdir "%HRS_RESULTS%"
rem /XX: don't list the files already in the results folder that this run didn't make.
robocopy "%~dp0work" "%HRS_RESULTS%" /E /XX /XF *.stormmap /NFL /NDL /NJH /NP
if errorlevel 8 (
  echo Copy FAILED - see the robocopy output above.
  exit /b 1
)
echo Results copied.
