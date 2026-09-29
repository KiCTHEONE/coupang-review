import os
from pathlib import Path

import pytest

from coupang_review.config import DEFAULTS
from coupang_review.site import CoupangEatsStore

playwright = pytest.importorskip("playwright.sync_api")


@pytest.fixture
def store():
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("PW_CHROMIUM") or None)
        ctx = browser.new_context()
        s = CoupangEatsStore(ctx, DEFAULTS["site"])
        s.page.goto((Path(__file__).parent / "fake_reviews.html").as_uri())
        yield s
        browser.close()


def test_parse_and_post(store):
    items = store.review_items()
    reviews = [store.parse_review(i) for i in items]
    assert [r.review_id for r in reviews] == ["r1", "r2", "r3"]
    assert reviews[0].author == "김**" and reviews[0].rating == 5 and reviews[0].menu == "후라이드치킨"
    assert reviews[0].text == "바삭하고 맛있어요!"
    assert [r.has_reply for r in reviews] == [False, False, True]

    assert store.post_reply(items[0], "감사합니다!")
    assert store.parse_review(store.review_items()[0]).has_reply
