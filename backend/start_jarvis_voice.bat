@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

set VOICEBOX_DIR=C:\Users\malma\AppData\Local\Voicebox
set VOICEBOX_EXE=%VOICEBOX_DIR%\voicebox-server.exe

rem --- Voicebox (cloned-voice server) has to run as its own process, started
rem     from its own install dir - otherwise it creates data files inside
rem     this repo. If it's not installed, just skip: Jarvis falls back to
rem     the offline voice on its own (see TTS_PROVIDER in .env).
rem
rem     All branching below uses single-line "if ... goto" and labels kept
rem     outside any parenthesized block on purpose - goto to a label defined
rem     *inside* an if/else ( ... ) block corrupts cmd's parsing of the rest
rem     of that block (a well-known batch gotcha), which silently ran the
rem     "not found" branch even when Voicebox was actually installed.
tasklist /FI "IMAGENAME eq voicebox-server.exe" 2>NUL | find /I "voicebox-server.exe" >NUL
if not errorlevel 1 goto voicebox_already_running
if not exist "%VOICEBOX_EXE%" goto voicebox_not_found

echo Starting Voicebox voice server...
start "" /D "%VOICEBOX_DIR%" "%VOICEBOX_EXE%"
call :wait_for_voicebox
goto run_jarvis

:voicebox_already_running
echo Voicebox is already running.
goto run_jarvis

:voicebox_not_found
echo Voicebox not found at %VOICEBOX_EXE% - skipping, Jarvis will fall back to the offline voice.
goto run_jarvis

:wait_for_voicebox
set TRIES=0
:wait_loop
rem ping-based delay, not "timeout" - timeout needs an interactive console
rem and errors out under any redirected stdin.
ping -n 2 127.0.0.1 >NUL
set /a TRIES+=1
curl -s -o NUL -w "%%{http_code}" http://127.0.0.1:8000/health > "%TEMP%\jarvis_vb_health.txt" 2>NUL
set /p VB_CODE=<"%TEMP%\jarvis_vb_health.txt"
if "!VB_CODE!"=="200" goto wait_success
if !TRIES! GEQ 25 goto wait_timed_out
goto wait_loop
:wait_success
echo Voicebox is ready.
goto wait_done
:wait_timed_out
echo Voicebox did not respond in time - Jarvis will fall back to the offline voice.
:wait_done
del "%TEMP%\jarvis_vb_health.txt" >NUL 2>NUL
exit /b

:run_jarvis
rem Voicebox runs windowless, so Windows treats it as background work and
rem parks it on the slow efficiency cores of this hybrid CPU (Core Ultra 9
rem 185H) - measured 170s for "Включаю свет." at normal priority vs 25s at
rem High. Applied whether we just started it or it was already running.
powershell -NoProfile -Command "Get-Process voicebox-server -ErrorAction SilentlyContinue | ForEach-Object { $_.PriorityClass = 'High' }" >NUL 2>&1
echo.
".venv\Scripts\python.exe" voice_app.py
echo.
pause
