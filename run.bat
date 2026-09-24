@echo off
rem Langmod Manager.
rem
rem   run.bat            open the window
rem   run.bat launch     apply again if the game or the mods changed, then start the game
rem   run.bat --help     every command
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call :makevenv || exit /b 1
if "%~1"=="" (
    start "" ".venv\Scripts\pythonw.exe" -m langmod
    exit /b 0
)
".venv\Scripts\python.exe" -m langmod %*
exit /b %errorlevel%

:makevenv
echo First run: making a Python environment and fetching PySide6. This takes a minute.
py -3.14 -m venv .venv 2>nul || python -m venv .venv 2>nul || goto nopython
".venv\Scripts\python.exe" -m pip install --upgrade pip PySide6
exit /b %errorlevel%

:nopython
echo Python 3.14 or newer is needed: https://www.python.org/downloads/
pause
exit /b 1
