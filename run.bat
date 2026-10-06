@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Creating .venv ...
  python -m venv .venv
)

call .venv\Scripts\activate.bat
python -m pip install -r requirements.txt
if not exist ".env" (
  copy /Y .env.example .env >nul
  echo Created .env from .env.example — DB password भर लो।
)

python start.py
pause
