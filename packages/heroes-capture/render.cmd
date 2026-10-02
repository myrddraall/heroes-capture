@echo off
rem Renders one battleground: prepares the map, captures it in the game, stitches the screenshots.
rem
rem   render.cmd                                     Towers of Doom, structures kept
rem   render.cmd "Cursed Hollow"                     another map
rem   render.cmd "Cursed Hollow" hide                bare terrain, structures hidden
rem   render.cmd "Towers of Doom" keep --fov 12      further options go to inject.mjs, except:
rem     --show-ui      diagnostic: leave the HUD up, launch the map and stop
rem     --probe-light  diagnostic: command sequences at chosen points, a shot after each
rem     --probe-sky    diagnostic: one edge tile over each solid-colour skybox
rem     --probe-depth  diagnostic: only measure the sky layers' parallax (renders do it after the tiles)
rem     --probe-waits  diagnostic: the fixed waits tried shorter, compared with the current ones
rem
rem Start Heroes from Battle.net first (so it is logged in), in Windowed (Fullscreen).
setlocal
rem pushd, not cd: it also works from a network path such as \\wsl.localhost\...
pushd "%~dp0"

set "MAP=%~1"
if "%MAP%"=="" set "MAP=Towers of Doom"
set "STRUCTURES=%~2"
if "%STRUCTURES%"=="" set "STRUCTURES=keep"
set "SCREEN=3440x1440"
rem Camera distance (the field of view is chosen to keep the scale): far, so tall objects lean
rem little at the seams. (The dark areas seen at far distances were the game's lighting fitted
rem to an old camera; the map script now refits it at every tile, so the distance is free.)
set "DISTANCE=214"
set "KEEP=0.4"
set "GAME=D:\Games\Heroes of the Storm"

rem Options after the map and structures go to inject.mjs as they are.
set "ARGS="
:collect
if "%~3"=="" goto :collected
set "ARGS=%ARGS% %3"
shift /3
goto :collect
:collected

rem Diagnostic switches are handled here, not by inject.mjs.
set "PROBE="
echo %ARGS% | find "--probe-light" >nul && set "PROBE=--probe-light"
echo %ARGS% | find "--probe-sky" >nul && set "PROBE=--probe-sky"
echo %ARGS% | find "--probe-depth" >nul && set "PROBE=--probe-depth"
echo %ARGS% | find "--probe-waits" >nul && set "PROBE=--probe-waits"
if defined PROBE set "ARGS=%ARGS:--probe-light=%"
if defined PROBE set "ARGS=%ARGS:--probe-sky=%"
if defined PROBE set "ARGS=%ARGS:--probe-depth=%"
if defined PROBE set "ARGS=%ARGS:--probe-waits=%"
set "SHOWUI="
echo %ARGS% | find "--show-ui" >nul && set "SHOWUI=1"

rem First run only: install what the scripts need.
if not exist node_modules (
  call npm install || goto :error
)
py -c "import mss, pydirectinput, PIL, numpy, pyvips, scipy, dxcam" 2>nul || py -m pip install -r requirements.txt || goto :error

echo.
echo [1/3] Preparing %MAP% (structures: %STRUCTURES%, %SCREEN%)
set "MANIFEST="
for /f "usebackq delims=" %%m in (`node inject.mjs "%MAP%" --structures %STRUCTURES% --screen %SCREEN% --distance %DISTANCE% --keep %KEEP%%ARGS%`) do set "MANIFEST=%%m"
if not defined MANIFEST goto :error

if defined PROBE (
  echo.
  echo Probe %PROBE%
  py capture.py "%MANIFEST%" --game "%GAME%" %PROBE% || goto :error
  popd
  exit /b 0
)
if defined SHOWUI (
  echo.
  echo Diagnostic run: launching the map and stopping here.
  py capture.py "%MANIFEST%" --game "%GAME%" --launch-only
  popd
  exit /b 0
)

rem Old screenshots would mix into this run.
if exist "%MANIFEST:.json=%\tiles" rmdir /s /q "%MANIFEST:.json=%\tiles"

echo.
echo [2/3] Capturing
py capture.py "%MANIFEST%" --game "%GAME%" || goto :error

echo.
echo [3/3] Stitching
py stitch.py "%MANIFEST%" --tiles || goto :error

echo.
echo Done. The image is next to %MANIFEST%
popd
exit /b 0

:error
echo.
echo Stopped: the step above failed.
popd
exit /b 1
