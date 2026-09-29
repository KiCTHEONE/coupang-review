from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass
class Review:
    author: str
    rating: int | None
    text: str
    menu: str = ""
    date: str = ""
    review_id: str = ""
    has_reply: bool = False
    ai_reply: str = ""  # 카드를 읽을 때 AI가 함께 써 둔 답글 (있으면 그대로 사용)

    @property
    def key(self) -> str:
        """리뷰 고유 키. 사이트에서 ID를 못 얻으면 내용 기반 해시를 사용."""
        if self.review_id:
            return self.review_id
        raw = f"{self.author}|{self.date}|{self.menu}|{self.text}"
        return "h:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class QuotaExhausted(Exception):
    """AI 사용 한도가 끝나 더 진행할 수 없음 (하루 한도 등)."""


class AIUnavailable(Exception):
    """AI 호출이 계속 실패함. 이 리뷰는 건너뛰고 다음 실행 때 다시 시도."""
