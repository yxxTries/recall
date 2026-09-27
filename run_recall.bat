@echo off
rem Starts Recall in the tray, without a console window. Logs: %LOCALAPPDATA%\Recall\logs\recall.log
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" -m recall
