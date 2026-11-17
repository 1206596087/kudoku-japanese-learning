@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
)
".venv\Scripts\python.exe" -c "import fastapi, uvicorn, sudachipy" >nul 2>&1
if errorlevel 1 (
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
)
echo.
echo KUDOKU is starting for LAN access at http://0.0.0.0:8000
echo Open this computer's LAN IP with port 8000 on your phone.
echo Press Ctrl+C to stop the server.
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000

