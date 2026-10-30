@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv-livekit\Scripts\python.exe" python -m venv .venv-livekit
if errorlevel 1 goto failure
".venv-livekit\Scripts\python.exe" -m pip install -r requirements-livekit.txt
if errorlevel 1 goto failure
".venv-livekit\Scripts\python.exe" scripts\configure_webrtc.py
if errorlevel 1 goto failure
echo Set LiveKit, Deepgram, Gemini and your synthetic CALLER_PHONE in .env.
notepad .env
echo Start with start-webrtc.bat. Stop any other server using port 8000 first.
pause
exit /b 0
:failure
echo Setup failed. Use Python 3.10 through 3.12 and check the installation output.
pause
exit /b 1
