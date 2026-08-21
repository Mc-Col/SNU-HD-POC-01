@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo ============================================
echo   D2S PoC - 개발 환경 설치
echo ============================================
echo.

where python > nul 2>&1
if errorlevel 1 (
  echo [실패] Python 이 설치되어 있지 않습니다.
  echo.
  echo   https://www.python.org/downloads/ 에서 설치한 뒤
  echo   설치 화면에서 "Add Python to PATH" 를 반드시 체크하세요.
  echo.
  pause
  exit /b 1
)

echo [1/3] 가상환경 생성...
if not exist ".venv" (
  python -m venv .venv
) else (
  echo       이미 있습니다. 넘어갑니다.
)

echo [2/3] 패키지 설치... (몇 분 걸립니다)
call .venv\Scripts\python.exe -m pip install --upgrade pip --quiet
call .venv\Scripts\python.exe -m pip install -r requirements.txt --quiet
if errorlevel 1 (
  echo [실패] 패키지 설치에 실패했습니다. 인터넷 연결을 확인하세요.
  pause
  exit /b 1
)

echo [3/3] API 키 파일 준비...
if not exist ".env" (
  echo ANTHROPIC_API_KEY=여기에_키를_붙여넣으세요 > .env
  echo       .env 파일을 만들었습니다. 메모장으로 열어 키를 넣어주세요.
) else (
  echo       이미 있습니다.
)

echo.
echo ============================================
echo   설치 완료
echo   이제 check_env.bat 을 더블클릭하세요.
echo ============================================
pause
