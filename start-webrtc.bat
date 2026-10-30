@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv-livekit\Scripts\python.exe" (
  echo Run setup-webrtc.bat first.
  pause
  exit /b 1
)
".venv-livekit\Scripts\python.exe" -m uvicorn telecom_support.livekit_server:app --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers
if errorlevel 1 (
  echo Check the error above. Stop any other server using port 8000 before starting.
  pause
)
