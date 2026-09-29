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
        "use_claude": True,
        "model": "claude-opus-5-5",
        "effort": "low",
    },
    "run": {
        "dry_run": True,  # true면 답글을 생성만 하고 등록하지 않음
        "max_pages": 3,
        "max_replies_per_run": 20,
        "delay_between_replies_sec": 3,
        "interval_minutes": 0,  # 0이면 1회 실행, 그 외엔 주기 반복
        "headless": True,
        "state_file": "state.json",
        "profile_dir": ".browser-profile",
    },
    "site": {
        "login_url": "https://store.coupangeats.com/merchant/login",
        "reviews_url": "https://store.coupangeats.com/merchant/management/reviews",
        # 사장님 사이트 화면 구조가 바뀌면 아래 셀렉터를 수정하세요.
        # `python -m coupang_review inspect` 로 현재 페이지 HTML/스크린샷을 저장해 확인할 수 있습니다.
        "selectors": {
            "login_id": "input[name='loginId'], input#loginId, input[type='text']",
            "login_password": "input[name='password'], input#password, input[type='password']",
            "login_submit": "button[type='submit']",
            "logged_in_marker": "text=로그아웃",
            "unanswered_filter": "",  # 예: "text=미답변" (비워두면 클릭 안 함)
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
