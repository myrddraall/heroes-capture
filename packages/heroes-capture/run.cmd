@echo off
rem What to run right now. Kept current with whatever the next test or render needs, so
rem update.cmd (which refreshes the files and then calls this) needs no arguments.
rem
rem Current step: Punisher Arena again (--force), so its pack gets the custom minimap as SVG
rem (maps\punisher-arena\pack\images\custom-minimap.svg, images.customMinimapSvg in pack.json) and
rem the new viewer. Afterwards, "py -m heroes_capture map view "Punisher Arena"" opens it (Ctrl+C
rem stops it): the minimap lies on the map while the first view loads, then fades away; the
rem Layers buttons show or hide the background, sky, haze, terrain and minimap. The minimap is
rem placed from the camera bounds and the walkable cells now, and its lines keep their width
rem however far you zoom.
rem A map that fails leaves its working files in tmp\ (pip, when it runs, writes tmp\setup.log).
setlocal
pushd "%~dp0"
set "PYTHONPATH=%~dp0src"
rem The tool's dependencies, installed quietly when missing (pip's output in tmp\setup.log).
py -c "import heroes_capture.cli, numpy, PIL, pyvips, scipy, mss, pydirectinput, dxcam, typer, rich, pmtiles" 2>nul || call :install || goto :copy
py -m heroes_capture map render "Punisher Arena" --force
if errorlevel 1 echo.& echo Stopped: the step above failed.
popd

:copy
rem Copy this run's output (logs\, maps\ and tmp\, without the prepared map files) to the results
rem folder, which update.cmd points at the development machine's tmp\ folder.
if not defined HRS_RESULTS (
  echo.
  echo Results not copied back: HRS_RESULTS is not set. Run this through update.cmd.
  exit /b 0
)
if not exist "%~dp0logs" if not exist "%~dp0tmp" if not exist "%~dp0maps" (
  echo.
  echo Nothing to copy back: the run left no logs, tmp or maps folder.
  exit /b 1
)
echo.
echo Copying results to %HRS_RESULTS% ...
set "FAILED="
rem /XX: don't list the files already in the results folder that this run didn't make. /NJS: no
rem summary table per folder (errors still print); the one line below says how it went.
for %%F in (logs tmp maps) do if exist "%~dp0%%F" (
  robocopy "%~dp0%%F" "%HRS_RESULTS%\%%F" /E /XX /XF *.stormmap /NFL /NDL /NJH /NJS /NP
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
