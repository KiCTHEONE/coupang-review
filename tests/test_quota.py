import json
from types import SimpleNamespace

import pytest
from google.genai import errors

from coupang_review import generator as gen
from coupang_review.generator import ReplyGenerator
from coupang_review.models import AIUnavailable, QuotaExhausted, Review


def _err(code, message):
    return errors.APIError(code, {"error": {"code": code, "message": message, "status": "X"}})


class FakeModels:
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def generate_content(self, **kw):
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(text=item)


@pytest.fixture
def make(monkeypatch):
    sleeps = []
    monkeypatch.setattr(gen.time, "sleep", lambda s: sleeps.append(s))

    def _make(script):
        g = ReplyGenerator({"name": "가게"}, {"api_key": "k", "max_length": 300, "min_interval_sec": 0})
        g.client = SimpleNamespace(models=FakeModels(script))
        return g, sleeps

    return _make


def test_retry_uses_server_delay(make):
    g, sleeps = make([_err(429, "Quota exceeded. Please retry in 12.5s."), "고마워요!"])
    assert g.generate(Review(author="a", rating=5, text="맛있어요")) == "고마워요!"
    assert sleeps == [14.5]


def test_daily_quota_stops(make):
    g, _ = make([_err(429, "Quota exceeded for metric: generate_content_free_tier_requests, limit: GenerateRequestsPerDayPerProjectPerModel-FreeTier")])
    with pytest.raises(QuotaExhausted):
        g.generate(Review(author="a", rating=5, text="맛있어요"))


def test_persistent_503_skips_not_template(make):
    g, _ = make([_err(503, "overloaded")] * 7)
    with pytest.raises(AIUnavailable):
        g.generate(Review(author="a", rating=5, text="맛있어요"))


def test_read_card_returns_reply_in_one_call(make):
    data = {"is_review": True, "author": "김**", "rating": 5, "menu": "치킨", "text": "맛있어요", "reply": "치킨이 치킨했네요!"}
    g, _ = make([json.dumps(data, ensure_ascii=False)])
    assert g.read_card("김**\n맛있어요", b"png")["reply"] == "치킨이 치킨했네요!"
    assert g.client.models.calls == 1
