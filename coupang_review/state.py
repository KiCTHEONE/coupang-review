"""이미 답글을 단 리뷰를 기록해 중복 등록을 막는다."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


class ReplyState:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: dict[str, dict] = {}
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))

    def has(self, key: str) -> bool:
        return key in self.data

    def add(self, key: str, reply: str, **meta) -> None:
        self.data[key] = {"reply": reply, "at": datetime.now().isoformat(timespec="seconds"), **meta}
        self.save()

    def save(self) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)
