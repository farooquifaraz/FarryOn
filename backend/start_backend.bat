@echo off
REM ============================================================
REM  FarryOn backend launcher — run this yourself and KEEP THIS
REM  WINDOW OPEN. Running the backend from your own terminal (not
REM  from Claude's tools) keeps it alive across sessions, so the
REM  phone app stops getting stuck on "Connecting...".
REM
REM  Double-click this file, OR from a terminal:  start_backend.bat
REM  Stop the server with Ctrl+C. Phone must be on the SAME Wi-Fi.
REM ============================================================
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
echo Starting FarryOn backend on http://0.0.0.0:8000  (Ctrl+C to stop)
echo Phone should connect to: ws://192.168.1.107:8000/ws/live
echo.
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
echo.
echo Backend stopped. Press any key to close.
pause >nul
