@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto install
where py >nul 2>nul
if errorlevel 1 goto pythonfallback
py -3 -m venv .venv
if not errorlevel 1 goto install
:pythonfallback
python -m venv .venv
if errorlevel 1 goto failure
:install
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failure
if exist .env goto ready
copy /y .env.example .env >nul
echo Enter your Gemini and Deepgram keys in the configuration file, then save it.
notepad .env
:ready
echo Setup complete. Double-click start.bat to open the voice assistant.
echo The first start builds a fictional demo knowledge index if none exists.
pause
exit /b 0
:failure
echo Setup failed. Install Python 3.10 through 3.12 and check your internet connection.
pause
exit /b 1
