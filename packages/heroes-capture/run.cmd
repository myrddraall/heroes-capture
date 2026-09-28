@echo off
rem What to run right now. Kept current with whatever the next test or render needs, so
rem update.cmd (which refreshes the files and then calls this) needs no arguments.
rem
rem Current step: the first full render placed by registration markers (Towers of Doom,
rem structures kept, field of view 8, markers on). About 8 minutes; the log, the marker fits
rem and the stitched image end up in the results folder.
call "%~dp0render.cmd" "Towers of Doom" keep

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
robocopy "%~dp0work" "%HRS_RESULTS%" /MIR /XF *.stormmap /NDL /NJH /NP
if errorlevel 8 (
  echo Copy FAILED - see the robocopy output above.
  exit /b 1
)
echo Results copied.
