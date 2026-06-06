@echo off
cd /d "%~dp0backend"
echo Starting backend on http://127.0.0.1:8080 ...
.venv\Scripts\python.exe -m uvicorn main:app --reload --port 8080 --host 127.0.0.1
