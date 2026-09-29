import copy
import os
from pathlib import Path

import pytest

from coupang_review.config import DEFAULTS
from coupang_review.site import CoupangEatsStore, card_key

playwright = pytest.importorskip("playwright.sync_api")
FIXTURES = Path(__file__).parent


@pytest.fixture
def open_store():
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("PW_CHROMIUM") or None)

        def _open(fixture, mode):
            cfg = copy.deepcopy(DEFAULTS["site"])
            cfg["mode"] = mode
            s = CoupangEatsStore(browser.new_context(), cfg)
            s.page.goto((FIXTURES / fixture).as_uri())
            return s

        yield _open
        browser.close()


def test_selectors_mode(open_store):
    store = open_store("fake_reviews.html", "selectors")
    items = store.review_items()
    reviews = [store.parse_review(i) for i in items]
    assert [r.review_id for r in reviews] == ["r1", "r2", "r3"]
    assert reviews[0].author == "김**" and reviews[0].rating == 5 and reviews[0].menu == "후라이드치킨"
    assert [r.has_reply for r in reviews] == [False, False, True]
    assert store.post_reply(items[0], "감사합니다!")
    assert store.parse_review(store.review_items()[0]).has_reply


def test_auto_mode_simple_page(open_store):
    store = open_store("fake_reviews.html", "auto")
    items = store.review_items()
    assert len(items) == 2  # 답글 있는 리뷰는 제외
    r = store.parse_review(items[0])
    assert r.author == "김**" and r.rating == 5 and r.text == "바삭하고 맛있어요!"
    assert store.post_reply(items[0], "바삭하게 드셔주셔서 감사합니다!")
    assert len(store.review_items()) == 1


def test_auto_mode_modal_and_confirm(open_store):
    store = open_store("fake_reviews_modal.html", "auto")
    store.open_reviews = lambda: None
    store.page.locator(store.sel["unanswered_filter"]).first.click()
    items = store.review_items()
    assert len(items) == 2
    r = store.parse_review(items[0])
    assert r.author == "김**" and r.rating == 5
    assert "양념이 달달" in r.text
    assert store.parse_review(items[1]).rating == 1

    assert store.post_reply(items[0], "맛있게 드셔주셔서 감사합니다!")
    assert store.page.evaluate("window.posted") == ["맛있게 드셔주셔서 감사합니다!"]
    assert len(store.review_items()) == 1


def test_card_key_ignores_relative_date():
    assert card_key("김**\n3일 전\n맛있어요") == card_key("김**\n4일 전\n맛있어요")
    assert card_key("김**\n맛있어요") != card_key("김**\n별로예요")
