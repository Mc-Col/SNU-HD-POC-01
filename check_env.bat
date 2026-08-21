@echo off
chcp 65001 > nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [실패] 가상환경이 없습니다. setup.bat 을 먼저 실행하세요.
  pause
  exit /b 1
)
call .venv\Scripts\python.exe tools\check_env.py
pause
