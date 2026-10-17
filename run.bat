@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

if exist ".venv\Scripts\python.exe" goto run

echo First run: creating virtual environment and installing dependencies...
python -m venv .venv
if errorlevel 1 goto error
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto error

:run
".venv\Scripts\python.exe" -m mediaviewer %*
if errorlevel 1 pause
goto :eof

:error
echo.
echo Setup failed. Python 3.11+ is required (python.org), ffmpeg is recommended for video.
pause
