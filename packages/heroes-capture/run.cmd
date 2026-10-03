@echo off
rem What to run right now. Kept current with whatever the next test or render needs, so
rem update.cmd (which refreshes the files and then calls this) needs no arguments.
rem
rem Current step: the first run from the heroes-capture repo (the tool moved there; nothing in it
rem changed): a full render of Dragon Shire, whose void is black terrain rather than sky.
call "%~dp0render.cmd" "Dragon Shire" keep

:copy
rem Copy this run's output (everything in work\ except the map files) to the results folder,
rem which update.cmd points at the development machine's tmp\ folder.
if not defined HRS_RESULTS (
  echo.
  echo Results not copied back: HRS_RESULTS is not set. Run this through update.cmd.
  exit /b 0
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
