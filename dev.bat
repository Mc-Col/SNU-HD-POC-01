@echo off
chcp 65001 > nul
cd /d "%~dp0"
if not exist ".venv\Scripts\activate.bat" (
  echo [실패] setup.bat 을 먼저 실행하세요.
  pause & exit /b 1
)
call .venv\Scripts\activate.bat
echo ============================================
echo   개발 환경 준비됨 (.venv 활성화)
echo   Claude 를 실행합니다...
echo ============================================
claude
