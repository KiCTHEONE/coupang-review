@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [1/3] 필요한 프로그램 설치 중...
python -m pip install -r requirements.txt || goto :fail
echo [2/3] 브라우저 설치 중...
python -m playwright install chromium || goto :fail
if not exist config.yaml copy config.example.yaml config.yaml >nul
echo [3/3] 설정 파일을 엽니다. 가게 이름, 서명, api_key 를 입력하고 저장하세요.
notepad config.yaml
echo 설치 완료! 다음은 2_로그인.bat 을 실행하세요.
pause
exit /b 0
:fail
echo 설치 중 오류가 났습니다. 위 메시지를 복사해서 보내주세요.
pause
exit /b 1
