@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto setupneeded
if not exist .env goto setupneeded
if exist "faiss_index\index.faiss" if exist "faiss_index\index.pkl" goto run
echo Preparing the fictional demo index. This uses the Gemini embedding API.
".venv\Scripts\python.exe" scripts\prepare_demo.py
if errorlevel 1 goto failure
:run
".venv\Scripts\python.exe" main.py %*
if errorlevel 1 goto failure
exit /b 0
:setupneeded
echo Please double-click setup.bat first and enter your API keys.
pause
exit /b 1
:failure
echo The assistant stopped with an error. Review the message above or docs\quickstart.md.
pause
exit /b 1
