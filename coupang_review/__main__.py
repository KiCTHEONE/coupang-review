import sys

try:
    from .main import main
except ModuleNotFoundError as e:
    sys.exit(
        f"필요한 프로그램이 설치되지 않았습니다 ({e.name}).\n"
        "이 폴더에서 아래 두 줄을 먼저 실행하거나 1_설치.bat 을 더블클릭하세요.\n"
        "  python -m pip install -r requirements.txt\n"
        "  python -m playwright install chromium"
    )

main()
