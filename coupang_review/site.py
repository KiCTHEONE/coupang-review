"""Playwright로 쿠팡이츠 사장님 사이트를 조작한다 (로그인, 리뷰 수집, 답글 등록).

두 가지 모드가 있다.
- auto (기본): HTML 구조를 몰라도 동작한다. "사장님 댓글 등록" 같은 버튼 글자로 미답변 리뷰 카드를 찾고,
  카드의 글자와 스크린샷을 AI가 읽어 작성자·별점·메뉴·내용을 추출한다.
- selectors: config의 CSS 셀렉터로 직접 지정한다 (auto가 맞지 않을 때).
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from pathlib import Path
from typing import Callable

from playwright.sync_api import BrowserContext, Locator, Page, TimeoutError as PWTimeout

from .models import QuotaExhausted, Review

log = logging.getLogger(__name__)

CARD_ATTR = "data-cr-card"
CLICKABLE = "button, a, [role=button]"

# 미답변 리뷰 카드를 찾아 data-cr-card 속성으로 표시한다.
# 답글 버튼에서 위로 올라가며, 형제 요소들과 같은 모양(리스트 항목)인 조상을 카드로 본다.
_MARK_CARDS_JS = """
([pattern, attr, clickable]) => {
  const re = new RegExp(pattern);
  document.querySelectorAll('[' + attr + ']').forEach(e => e.removeAttribute(attr));
  const label = el => (el.innerText || el.textContent || '').trim();
  const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const isOpener = el => re.test(label(el)) && visible(el);
  const countOpeners = el => [...el.querySelectorAll(clickable)].filter(isOpener).length;
  const listy = el => ['LI', 'ARTICLE', 'TR'].includes(el.tagName) || (typeof el.className === 'string' && el.className.trim());
  const sameShape = (a, b) => a.tagName === b.tagName && a.className === b.className;
  const cards = [];
  for (const btn of [...document.querySelectorAll(clickable)].filter(isOpener)) {
    let el = btn, card = null;
    while (el.parentElement && el.parentElement !== document.body) {
      const parent = el.parentElement;
      if (countOpeners(parent) > 1) { card = el; break; }
      const siblings = [...parent.children].filter(c => c !== el && sameShape(c, el));
      const extra = (el.innerText || '').length - label(btn).length;
      if (siblings.length && el !== btn && listy(el) && extra > 10) { card = el; break; }
      el = parent;
    }
    card = card || el;
    if (!cards.includes(card)) cards.push(card);
  }
  cards.forEach((c, i) => c.setAttribute(attr, String(i)));
  return cards.length;
}
"""

# 답글 입력창에서 위로 올라가며 가장 가까운 '등록' 버튼을 찾아 클릭한다.
_CLICK_SUBMIT_JS = """
(ta, [pattern, clickable]) => {
  const re = new RegExp(pattern);
  const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  let el = ta.parentElement;
  while (el) {
    const btn = [...el.querySelectorAll(clickable)].find(b =>
      visible(b) && !b.disabled && re.test((b.innerText || b.textContent || '').trim()));
    if (btn) { btn.click(); return (btn.innerText || '').trim(); }
    el = el.parentElement;
  }
  return null;
}
"""

_DIALOG_OK = (
    ":is([role=dialog], [role=alertdialog], [class*='modal'], [class*='Modal'], [class*='dialog'])"
    " :is(button, [role=button]):visible >> text=/^\\s*(확인|예|네)\\s*$/"
)

# 방금 입력한 답글이 아직 입력창에 남아 있는지 (남아 있으면 등록 안 된 것)
_DRAFT_VISIBLE_JS = """
(snippet) => [...document.querySelectorAll('textarea, [contenteditable=true]')].some(el =>
  !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length) &&
  (el.value || el.innerText || '').includes(snippet))
"""

# 카드 안의 글자를 화면 요소 단위로 한 줄씩 뽑는다 (inner_text는 옆에 붙은 글자를 한 줄로 합침).
_CARD_LINES_JS = """
(card) => {
  const out = [];
  const walk = el => {
    if (!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)) return;
    let own = '';
    for (const n of el.childNodes) {
      if (n.nodeType === 3) own += n.textContent;
      else if (n.nodeType === 1) { if (own.trim()) out.push(own.trim()); own = ''; walk(n); }
    }
    if (own.trim()) out.push(own.trim());
  };
  walk(card);
  return out.join('\\n');
}
"""

_DATE_RE = re.compile(
    r"\d+\s*(초|분|시간|일|주|개월|달|년)\s*전|\d{2,4}\s*[.\-/년]\s*\d{1,2}\s*[.\-/월]\s*\d{1,2}\s*일?|어제|오늘"
)
_RATING_RE = re.compile(r"^(?:별점\s*)?([1-5])(?:\.0)?\s*점?$")

# (card_text, card_png) -> dict(author, rating, menu, text, is_review)
CardReader = Callable[[str, bytes], dict]


def card_key(card_text: str) -> str:
    """카드 글자에서 '3일 전' 같은 바뀌는 날짜를 지운 뒤 해시. 다음 실행 때도 같은 리뷰로 인식된다."""
    stable = _DATE_RE.sub("", card_text)
    stable = re.sub(r"\s+", " ", stable).strip()
    return "c:" + hashlib.sha256(stable.encode("utf-8")).hexdigest()[:16]


def heuristic_read(card_text: str, reply_button_re: str) -> dict:
    """AI 없이 카드 글자만으로 대략 추출한다."""
    btn = re.compile(reply_button_re)
    lines = [ln.strip() for ln in card_text.splitlines() if ln.strip()]
    rating = None
    stars = card_text.count("★")
    if 1 <= stars <= 5:
        rating = stars
    body = []
    for ln in lines:
        m = _RATING_RE.match(ln)
        if m and rating is None:
            rating = int(m.group(1))
            continue
        if btn.search(ln) or _DATE_RE.fullmatch(ln) or m:
            continue
        body.append(ln)
    author = body[0] if body else ""
    rest = body[1:] if len(body) > 1 else body
    text = max(rest, key=len) if rest else ""
    return {"author": author, "rating": rating or 0, "menu": "", "text": text, "is_review": bool(body)}


class CoupangEatsStore:
    def __init__(self, context: BrowserContext, site_cfg: dict, reader: CardReader | None = None):
        self.context = context
        self.cfg = site_cfg
        self.sel = site_cfg["selectors"]
        self.mode = site_cfg.get("mode", "auto")
        self.reply_button_re = site_cfg.get("reply_button_text", r"^(사장님\s*)?(댓글|답글)\s*(등록|작성|달기|쓰기)")
        self.submit_button_re = site_cfg.get("submit_button_text", r"^(등록|등록하기|작성|작성하기|완료|저장|확인)$")
        self.reader = reader
        self.page: Page = context.pages[0] if context.pages else context.new_page()

    # ---------- 로그인 ----------
    def _on_login_page(self) -> bool:
        if "login" in self.page.url:
            return True
        return self.page.locator("input[type='password']").count() > 0

    def ensure_login(self) -> None:
        self.page.goto(self.cfg["reviews_url"], wait_until="domcontentloaded")
        self._settle()
        if not self._on_login_page():
            return
        user, pw = os.environ.get("COUPANG_EATS_ID"), os.environ.get("COUPANG_EATS_PW")
        if not (user and pw):
            raise RuntimeError(
                "로그인이 필요합니다. `python -m coupang_review login` 으로 브라우저에서 직접 로그인하거나 "
                "COUPANG_EATS_ID / COUPANG_EATS_PW 환경변수를 설정하세요."
            )
        log.info("아이디/비밀번호로 로그인 시도")
        if "login" not in self.page.url:
            self.page.goto(self.cfg["login_url"], wait_until="domcontentloaded")
        self.page.locator(self.sel["login_id"]).first.fill(user)
        self.page.locator(self.sel["login_password"]).first.fill(pw)
        self.page.locator(self.sel["login_submit"]).first.click()
        self._settle()
        self.page.goto(self.cfg["reviews_url"], wait_until="domcontentloaded")
        self._settle()
        if self._on_login_page():
            raise RuntimeError(
                "자동 로그인 실패 (추가 인증/캡차일 수 있음). `python -m coupang_review login` 으로 직접 로그인하세요."
            )

    def _settle(self, timeout: int = 15000) -> None:
        try:
            self.page.wait_for_load_state("networkidle", timeout=timeout)
        except PWTimeout:
            pass

    # ---------- 리뷰 수집 ----------
    def open_reviews(self) -> None:
        self.page.goto(self.cfg["reviews_url"], wait_until="domcontentloaded")
        self._settle()
        self.apply_period()
        if self.sel.get("unanswered_filter"):
            try:
                self.page.locator(self.sel["unanswered_filter"]).first.click(timeout=3000)
                self._settle()
                log.info("미답변 필터 적용")
            except PWTimeout:
                log.debug("미답변 필터 없음: %s", self.sel["unanswered_filter"])
        time.sleep(1)  # 목록 렌더링 대기

    # ---------- 조회 기간 ----------
    def _click_text(self, texts: list[str]) -> str | None:
        """화면에 보이는 요소 중 글자가 정확히 일치하는 것을 우선순위대로 찾아 클릭."""
        for t in texts:
            loc = self.page.get_by_text(re.compile(rf"^\s*{re.escape(t)}\s*$")).locator("visible=true")
            if loc.count():
                try:
                    loc.first.click(timeout=2000)
                    return t
                except PWTimeout:
                    continue
        return None

    def apply_period(self) -> None:
        """리뷰 조회 기간을 설정된 가장 긴 기간으로 바꾼다 (기본 1년, 없으면 6개월→3개월…)."""
        periods = list(self.cfg.get("period_texts") or [])
        if not periods:
            return
        chosen = self._click_text(periods) or self._select_option(periods)
        if not chosen:
            # 기간 선택이 드롭다운/달력에 숨어 있으면 현재 기간(예: '오늘', '2026.09.29 ~ 2026.09.29')을 눌러 펼친 뒤 다시 찾는다.
            for opener in self._period_openers():
                try:
                    opener.click(timeout=2000)
                except Exception:
                    continue
                time.sleep(0.7)
                chosen = self._click_text(periods) or self._select_option(periods)
                if chosen:
                    break
                self.page.keyboard.press("Escape")
        if not chosen:
            chosen = self._fill_date_inputs()
        if not chosen:
            log.warning("조회 기간을 바꾸지 못했습니다. 화면에 보이는 기본 기간으로 진행합니다.")
            log.warning("화면 위쪽 버튼 글자: %s", " | ".join(self._visible_controls()))
            return
        time.sleep(0.5)
        self._click_text(self.cfg.get("period_apply_texts") or [])  # '조회' 같은 버튼이 있으면 누름
        self._settle()
        time.sleep(1)
        log.info("조회 기간: %s", chosen)

    def _select_option(self, texts: list[str]) -> str | None:
        """<select> 드롭다운에서 기간 항목을 고른다."""
        selects = self.page.locator("select:visible")
        for i in range(selects.count()):
            options = [o.strip() for o in selects.nth(i).locator("option").all_inner_texts()]
            for t in texts:
                match = next((o for o in options if re.fullmatch(rf"(최근\s*)?{re.escape(t)}", o)), None)
                if match:
                    selects.nth(i).select_option(label=match)
                    return t
        return None

    def _period_openers(self) -> list[Locator]:
        """눌렀을 때 기간 선택창이 열릴 만한 요소들."""
        found = []
        pattern = self.cfg.get("period_opener_text")
        if pattern:
            loc = self.page.get_by_text(re.compile(pattern)).locator("visible=true")
            found += [loc.nth(i) for i in range(min(loc.count(), 3))]
        date_range = re.compile(r"\d{4}\s*[.\-/]\s*\d{1,2}\s*[.\-/]\s*\d{1,2}")
        loc = self.page.get_by_text(date_range).locator("visible=true")
        found += [loc.nth(i) for i in range(min(loc.count(), 2))]
        inputs = self.page.locator("input:visible")
        for i in range(min(inputs.count(), 20)):
            if date_range.search(inputs.nth(i).input_value() or ""):
                found.append(inputs.nth(i))
                break
        return found

    def _visible_controls(self) -> list[str]:
        """진단용: 화면 위쪽에 보이는 짧은 버튼/탭/입력칸 글자."""
        return self.page.evaluate("""() => {
          const out = [];
          const vis = el => { const r = el.getBoundingClientRect(); return r.width && r.height && r.top < 700; };
          document.querySelectorAll('button, a, [role=tab], [role=button], [role=option], label, li, select, input, span, div').forEach(el => {
            if (!vis(el)) return;
            let t = '';
            if (el.tagName === 'INPUT') t = el.value || el.placeholder || '';
            else if (el.tagName === 'SELECT') t = 'select[' + [...el.options].map(o => o.text.trim()).join('/') + ']';
            else if (el.children.length === 0 || ['BUTTON', 'A', 'LABEL'].includes(el.tagName)) t = (el.innerText || '').trim();
            t = t.replace(/\s+/g, ' ');
            if (t && t.length <= 25 && !out.includes(t)) out.push(t);
          });
          return out.slice(0, 80);
        }""")

    def _fill_date_inputs(self) -> str | None:
        """시작일/종료일 입력칸이 있으면 1년 전 ~ 오늘로 입력."""
        from datetime import date, timedelta

        inputs = self.page.locator("input:visible")
        dated = []
        for i in range(min(inputs.count(), 20)):
            val = inputs.nth(i).input_value()
            m = re.fullmatch(r"(\d{4})([.\-/])(\d{1,2})\2(\d{1,2})\.?", val.strip())
            if m:
                dated.append((inputs.nth(i), m.group(2)))
        if len(dated) < 2:
            return None
        start = date.today() - timedelta(days=int(self.cfg.get("period_days", 365)))
        (box, sep) = dated[0]
        try:
            box.fill(start.strftime(f"%Y{sep}%m{sep}%d"), timeout=2000)
            box.press("Enter")
            return f"{start} ~ 오늘"
        except Exception:  # 읽기 전용 달력 입력칸 등
            return None

    def load_more(self) -> bool:
        """'더보기' 버튼이나 스크롤로 목록을 더 불러온다. 새 내용이 생기면 True."""
        size_js = "[document.getElementsByTagName('*').length, document.documentElement.scrollHeight]"
        before = self.page.evaluate(size_js)
        if not self._click_text(self.cfg.get("load_more_texts") or []):
            self.page.evaluate("""() => {
              window.scrollTo(0, document.body.scrollHeight);
              document.querySelectorAll('*').forEach(el => {
                if (el.scrollHeight > el.clientHeight + 50 && /(auto|scroll)/.test(getComputedStyle(el).overflowY))
                  el.scrollTop = el.scrollHeight;
              });
            }""")
        self._settle(5000)
        time.sleep(1.5)
        after = self.page.evaluate(size_js)
        return after[0] > before[0] or after[1] > before[1]

    def review_items(self) -> list[Locator]:
        if self.mode == "auto":
            n = self.page.evaluate(_MARK_CARDS_JS, [self.reply_button_re, CARD_ATTR, CLICKABLE])
            return [self.page.locator(f"[{CARD_ATTR}='{i}']") for i in range(n)]
        try:
            self.page.locator(self.sel["review_item"]).first.wait_for(timeout=10000)
        except PWTimeout:
            return []
        return self.page.locator(self.sel["review_item"]).all()

    def item_key(self, item: Locator) -> str | None:
        """AI 호출 전에 알 수 있는 리뷰 키 (auto 모드)."""
        if self.mode != "auto":
            return None
        return card_key(self._card_text(item))

    @staticmethod
    def _card_text(item: Locator) -> str:
        return item.evaluate(_CARD_LINES_JS)

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

    def parse_review(self, item: Locator) -> Review | None:
        """리뷰 정보를 읽는다. 리뷰가 아닌 카드면 None."""
        if self.mode == "auto":
            item.scroll_into_view_if_needed()
            text = self._card_text(item)
            data = None
            if self.reader:
                try:
                    data = self.reader(text, item.screenshot())
                except QuotaExhausted:
                    raise
                except Exception as e:  # 읽기 실패 시 글자 기반 추출로 대체
                    log.warning("AI로 카드 읽기 실패, 글자 기반으로 추출: %s", e)
            data = data or heuristic_read(text, self.reply_button_re)
            if not data.get("is_review", True):
                return None
            return Review(
                review_id=card_key(text),
                author=data.get("author", ""),
                rating=data.get("rating") or None,
                text=data.get("text", ""),
                menu=data.get("menu", ""),
                ai_reply=data.get("reply", ""),
                has_reply=False,  # auto 모드는 '댓글 등록' 버튼이 있는 카드만 찾으므로 항상 미답변
            )
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
            if btn.count() == 0 or not btn.is_visible() or not btn.is_enabled():
                return False
            btn.click(timeout=3000)
            self._settle()
            time.sleep(1)
            return True
        except PWTimeout:
            return False

    # ---------- 답글 등록 ----------
    def post_reply(self, item: Locator, reply: str) -> bool:
        if self.mode == "auto":
            return self._post_reply_auto(item, reply)
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
        self._settle()
        try:
            item.locator(self.sel["reply_exists"]).first.wait_for(timeout=8000)
            return True
        except PWTimeout:
            return False

    def _visible_textarea(self, item: Locator) -> Locator | None:
        for scope in (item, self.page):
            boxes = scope.locator("textarea:visible, [contenteditable='true']:visible")
            if boxes.count():
                return boxes.last
        return None

    def _post_reply_auto(self, item: Locator, reply: str) -> bool:
        item.scroll_into_view_if_needed()
        opener = item.locator(CLICKABLE).filter(has_text=re.compile(self.reply_button_re)).first
        opener.click()

        textarea = None
        for _ in range(20):
            textarea = self._visible_textarea(item)
            if textarea:
                break
            time.sleep(0.25)
        if textarea is None:
            log.error("답글 입력창을 찾지 못했습니다.")
            return False
        textarea.click()
        textarea.fill(reply)

        dialog_buttons = self.page.locator(_DIALOG_OK)
        before = dialog_buttons.count()
        clicked = textarea.evaluate(_CLICK_SUBMIT_JS, [self.submit_button_re, CLICKABLE])
        if clicked is None:
            log.error("답글 등록 버튼을 찾지 못했습니다.")
            return False
        log.debug("'%s' 버튼 클릭", clicked)

        # 등록 확인 창이 새로 뜨면 '확인'을 누르고, 답글이 담긴 입력창이 사라지면 성공으로 본다.
        confirmed = False
        for _ in range(40):
            time.sleep(0.25)
            if not confirmed and dialog_buttons.count() > before:
                dialog_buttons.last.click()
                confirmed = True
            if not self.page.evaluate(_DRAFT_VISIBLE_JS, reply[:20]):
                self._settle(5000)
                return True
        return False

    # ---------- 디버그 ----------
    def dump(self, out_dir: str | Path = "debug") -> Path:
        out = Path(out_dir)
        out.mkdir(exist_ok=True)
        self.page.screenshot(path=str(out / "reviews.png"), full_page=True)
        (out / "reviews.html").write_text(self.page.content(), encoding="utf-8")
        return out
