@echo off
rem What to run right now. Kept current with whatever the next test or render needs, so
rem update.cmd (which refreshes the files and then calls this) needs no arguments.
rem
rem Current step: animation-pause probe on Dragon Shire, at a tile with water and glows (the
rem river by the left base). Each entry: the tile, then a command, then two shots 0.5 s apart
rem with the share of pixels that moved between them logged: nothing (control); "pause" (the
rem whole-map AnimSetPausedAll message Blizzard's maps use at game over); the control again;
rem "pause 0" (resume); then pause followed by the black-sky swap, to see the swap still works
rem while paused. About 3 minutes.
set "HRS_PROBE_POINTS=40,92"
set "HRS_PROBE_TILE_PATH=tile:1;tile,pause:1;tile:1;tile,pause 0:1;tile,pause,black:1"
call "%~dp0render.cmd" "Dragon Shire" keep --probe-light

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
