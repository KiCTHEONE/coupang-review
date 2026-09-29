from coupang_review.generator import ReplyGenerator, template_reply
from coupang_review.models import Review
from coupang_review.state import ReplyState


def test_template_reply_uses_signature_and_limit(monkeypatch):
    r = Review(author="김**", rating=5, text="맛있어요", menu="치킨")
    text = template_reply(r, {"signature": "- 사장 드림"}, max_length=300)
    assert text.endswith("- 사장 드림") and "김**" in text
    assert len(template_reply(r, {"signature": ""}, max_length=20)) <= 20


def test_generator_without_key_falls_back(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    g = ReplyGenerator({"name": "가게"}, {"use_claude": True, "max_length": 300})
    assert g.client is None
    assert "죄송" in g.generate(Review(author="a", rating=1, text="별로"))


def test_review_key_stable():
    a = Review(author="a", rating=5, text="t", date="d")
    assert a.key == Review(author="a", rating=5, text="t", date="d").key
    assert Review(author="a", rating=5, text="t", review_id="x").key == "x"


def test_state_roundtrip(tmp_path):
    s = ReplyState(tmp_path / "s.json")
    s.add("k1", "reply")
    assert ReplyState(tmp_path / "s.json").has("k1")
