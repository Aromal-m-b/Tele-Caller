@echo off
setlocal
cd /d "%~dp0"
set "phonePython=python"
if exist ".venv\Scripts\python.exe" set "phonePython=%~dp0.venv\Scripts\python.exe"
"%phonePython%" -m uvicorn telecom_support.phone_server:app --host 127.0.0.1 --port 8000 --workers 1 --ws-max-size 32768 --no-proxy-headers
if errorlevel 1 (
  echo Install requirements-phone.txt in this Python environment and check the port.
  pause
)
