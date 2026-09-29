"""Playwright로 쿠팡이츠 사장님 사이트를 조작한다 (로그인, 리뷰 수집, 답글 등록)."""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from playwright.sync_api import BrowserContext, Locator, Page, TimeoutError as PWTimeout

from .models import Review

log = logging.getLogger(__name__)


class CoupangEatsStore:
    def __init__(self, context: BrowserContext, site_cfg: dict):
        self.context = context
        self.cfg = site_cfg
        self.sel = site_cfg["selectors"]
        self.page: Page = context.pages[0] if context.pages else context.new_page()

    # ---------- 로그인 ----------
    def is_logged_in(self) -> bool:
        try:
            self.page.locator(self.sel["logged_in_marker"]).first.wait_for(timeout=5000)
            return True
        except PWTimeout:
            return False

    def ensure_login(self) -> None:
        self.page.goto(self.cfg["reviews_url"], wait_until="domcontentloaded")
        self.page.wait_for_load_state("networkidle")
        if "login" not in self.page.url and self.is_logged_in():
            return
        user, pw = os.environ.get("COUPANG_EATS_ID"), os.environ.get("COUPANG_EATS_PW")
        if not (user and pw):
            raise RuntimeError(
                "로그인이 필요합니다. `python -m coupang_review login` 으로 브라우저에서 직접 로그인하거나 "
                "COUPANG_EATS_ID / COUPANG_EATS_PW 환경변수를 설정하세요."
            )
        log.info("아이디/비밀번호로 로그인 시도")
        self.page.goto(self.cfg["login_url"], wait_until="domcontentloaded")
        self.page.locator(self.sel["login_id"]).first.fill(user)
        self.page.locator(self.sel["login_password"]).first.fill(pw)
        self.page.locator(self.sel["login_submit"]).first.click()
        self.page.wait_for_load_state("networkidle")
        self.page.goto(self.cfg["reviews_url"], wait_until="domcontentloaded")
        self.page.wait_for_load_state("networkidle")
        if "login" in self.page.url:
            raise RuntimeError(
                "자동 로그인 실패 (추가 인증/캡차일 수 있음). `python -m coupang_review login` 으로 직접 로그인하세요."
            )

    # ---------- 리뷰 수집 ----------
    def open_reviews(self) -> None:
        self.page.goto(self.cfg["reviews_url"], wait_until="domcontentloaded")
        self.page.wait_for_load_state("networkidle")
        if self.sel.get("unanswered_filter"):
            try:
                self.page.locator(self.sel["unanswered_filter"]).first.click(timeout=5000)
                self.page.wait_for_load_state("networkidle")
            except PWTimeout:
                log.warning("미답변 필터를 찾지 못했습니다: %s", self.sel["unanswered_filter"])

    def review_items(self) -> list[Locator]:
        try:
            self.page.locator(self.sel["review_item"]).first.wait_for(timeout=10000)
        except PWTimeout:
            return []
        return self.page.locator(self.sel["review_item"]).all()

    @staticmethod
    def _text(item: Locator, selector: str) -> str:
        if not selector:
            return ""
        loc = item.locator(selector)
        return loc.first.inner_text().strip() if loc.count() else ""

    def _rating(self, item: Locator) -> int | None:
        if self.sel.get("filled_star"):
            n = item.locator(self.sel["filled_star"]).count()
            if 1 <= n <= 5:
                return n
        raw = self._text(item, self.sel.get("rating", ""))
        if not raw and self.sel.get("rating"):
            loc = item.locator(self.sel["rating"])
            if loc.count():
                raw = loc.first.get_attribute("aria-label") or ""
        m = re.search(r"([1-5])(?:\.0)?", raw)
        return int(m.group(1)) if m else None

    def parse_review(self, item: Locator) -> Review:
        attr = self.sel.get("review_id_attr")
        return Review(
            review_id=(item.get_attribute(attr) or "") if attr else "",
            author=self._text(item, self.sel["author"]),
            rating=self._rating(item),
            text=self._text(item, self.sel["text"]),
            menu=self._text(item, self.sel.get("menu", "")),
            date=self._text(item, self.sel.get("date", "")),
            has_reply=item.locator(self.sel["reply_exists"]).count() > 0,
        )

    def next_page(self) -> bool:
        btn = self.page.locator(self.sel["next_page"]).first
        try:
            if btn.count() == 0 or not btn.is_enabled():
                return False
            btn.click()
            self.page.wait_for_load_state("networkidle")
            return True
        except PWTimeout:
            return False

    # ---------- 답글 등록 ----------
    def post_reply(self, item: Locator, reply: str) -> bool:
        item.scroll_into_view_if_needed()
        item.locator(self.sel["reply_button"]).first.click()
        textarea = item.locator(self.sel["reply_textarea"]).first
        if textarea.count() == 0:  # 답글 입력창이 모달로 뜨는 경우
            textarea = self.page.locator(self.sel["reply_textarea"]).first
        textarea.wait_for(timeout=5000)
        textarea.fill(reply)
        submit = item.locator(self.sel["reply_submit"]).first
        if submit.count() == 0:
            submit = self.page.locator(self.sel["reply_submit"]).first
        submit.click()
        self.page.wait_for_load_state("networkidle")
        try:
            item.locator(self.sel["reply_exists"]).first.wait_for(timeout=8000)
            return True
        except PWTimeout:
            return False

    # ---------- 디버그 ----------
    def dump(self, out_dir: str | Path = "debug") -> Path:
        out = Path(out_dir)
        out.mkdir(exist_ok=True)
        self.page.screenshot(path=str(out / "reviews.png"), full_page=True)
        (out / "reviews.html").write_text(self.page.content(), encoding="utf-8")
        return out
