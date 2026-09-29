"""설정 파일(config.yaml) 로딩."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "store": {
        "name": "우리 가게",
        "signature": "",  # 답글 끝에 붙일 서명 (예: "- OO치킨 사장 드림")
        "tone": "따뜻하고 정중한 존댓말",
        "extra_instructions": "",
    },
    "reply": {
        "min_rating_to_auto_reply": 3,  # 이 별점 미만은 자동 등록하지 않고 건너뜀 (사장님 직접 확인)
        "max_length": 300,
        "use_ai": True,
        "api_key": "",  # Gemini API 키 (비우면 GEMINI_API_KEY 환경변수)
        "model": "gemini-flash-latest",
    },
    "run": {
        "dry_run": True,  # true면 답글을 생성만 하고 등록하지 않음
        "max_pages": 3,
        "max_replies_per_run": 20,
        "delay_between_replies_sec": 3,
        "interval_minutes": 0,  # 0이면 1회 실행, 그 외엔 주기 반복
        "headless": False,  # 쿠팡이츠가 headless 브라우저를 차단할 수 있어 기본은 창을 띄움
        "state_file": "state.json",
        # real: 컴퓨터에 설치된 크롬/엣지를 띄워 연결 (쿠팡이츠 로그인 차단 회피, 권장)
        # bundled: Playwright 내장 브라우저 사용
        "browser_mode": "real",
        "browser_executable": "",  # 크롬 경로를 직접 지정할 때
        "browser_args": [],  # 브라우저에 추가로 넘길 옵션
        "cdp_port": 9222,
        "chrome_profile_dir": ".chrome-profile",
        "session_file": "session.json",
        "profile_dir": ".browser-profile",  # bundled 모드용
    },
    "site": {
        # auto: HTML 구조를 몰라도 '댓글 등록' 버튼 글자로 리뷰를 찾고 Gemini가 카드를 읽음 (권장)
        # selectors: 아래 CSS 셀렉터를 직접 지정해서 사용
        "mode": "auto",
        "reply_button_text": r"^(사장님\s*)?(댓글|답글)\s*(등록|작성|달기|쓰기)",
        "submit_button_text": r"^(등록|등록하기|작성|작성하기|완료|저장|확인)$",
        "login_url": "https://store.coupangeats.com/merchant/login",
        "reviews_url": "https://store.coupangeats.com/merchant/management/reviews",
        # login_*, unanswered_filter, next_page 는 두 모드 모두 사용. 나머지는 selectors 모드 전용.
        "selectors": {
            "login_id": "input[name='loginId'], input#loginId, input[type='text']",
            "login_password": "input[name='password'], input#password, input[type='password']",
            "login_submit": "button[type='submit']",
            "logged_in_marker": "text=로그아웃",
            "unanswered_filter": "text=/^\\s*미답변/",  # 있으면 클릭, 없으면 무시
            "review_item": "[class*='review-item'], [class*='ReviewItem'], li[class*='review']",
            "review_id_attr": "data-review-id",  # 리뷰 요소에 고유 ID 속성이 있으면 사용
            "author": "[class*='nickname'], [class*='name']",
            "rating": "[class*='rating'], [class*='star']",
            "filled_star": "[class*='star'][class*='active'], [class*='star'][class*='fill']",
            "text": "[class*='content'], [class*='text'], p",
            "menu": "[class*='menu'], [class*='order']",
            "date": "[class*='date'], time",
            "reply_exists": "[class*='reply'][class*='content'], [class*='owner-reply'], [class*='comment-content']",
            "reply_button": "button:has-text('사장님 댓글 등록'), button:has-text('댓글 등록'), button:has-text('답글')",
            "reply_textarea": "textarea",
            "reply_submit": "button:has-text('등록'):not(:has-text('댓글 등록'))",
            "next_page": "button[aria-label='다음'], button:has-text('다음'), [class*='pagination'] [class*='next']",
        },
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: str | Path | None) -> dict[str, Any]:
    if path and Path(path).exists():
        with open(path, encoding="utf-8") as f:
            return _merge(DEFAULTS, yaml.safe_load(f) or {})
    return copy.deepcopy(DEFAULTS)
