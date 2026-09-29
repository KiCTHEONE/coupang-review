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

    @property
    def key(self) -> str:
        """리뷰 고유 키. 사이트에서 ID를 못 얻으면 내용 기반 해시를 사용."""
        if self.review_id:
            return self.review_id
        raw = f"{self.author}|{self.date}|{self.menu}|{self.text}"
        return "h:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
