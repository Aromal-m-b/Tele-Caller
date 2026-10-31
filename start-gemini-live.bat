@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv-livekit\Scripts\python.exe" (
  echo Run setup-webrtc.bat first.
  pause
  exit /b 1
)
".venv-livekit\Scripts\python.exe" -m uvicorn telecom_support.gemini_live_server:app --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers
if errorlevel 1 (
  echo Stop the existing server on port 8000 before switching backends.
  pause
)
