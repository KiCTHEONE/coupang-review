@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 답글을 만들어 보여주기만 합니다 (등록하지 않음).
python -m coupang_review run --dry-run --headful
pause
