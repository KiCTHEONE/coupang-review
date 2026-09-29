@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 미답변 리뷰에 답글을 실제로 등록합니다.
python -m coupang_review run --post
pause
