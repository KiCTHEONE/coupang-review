import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest
import yaml

from coupang_review import ui


@pytest.fixture
def server(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump({"run": {"state_file": str(tmp_path / "s.json"), "session_file": str(tmp_path / "x.json")}}))
    monkeypatch.setattr(ui, "PORT", 18765)
    app = ui.App(cfg)
    srv = ThreadingHTTPServer(("127.0.0.1", 18765), ui._handler(app))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield cfg
    srv.shutdown()


def _req(path, body=None, headers=None):
    h = {"Content-Type": "application/json", **(headers or {})}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:18765{path}", data=data, headers=h, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"{}") if "json" in r.headers["Content-Type"] else r.read()
    except urllib.error.HTTPError as e:
        return e.code, None


def test_page_and_state(server):
    code, body = _req("/")
    assert code == 200 and "리뷰 답글 관리".encode() in body
    code, st = _req("/api/state")
    assert code == 200 and st["job"] is None and st["has_session"] is False


def test_post_requires_app_header(server):
    assert _req("/api/stop", {})[0] == 403
    assert _req("/api/stop", {}, {"X-Review-App": "1"})[0] == 200


def test_settings_save_hides_key(server):
    code, r = _req("/api/settings", {"name": "우리치킨", "min_rating": 1, "api_key": "AIza-secret", "tone": "웃기게"},
                   {"X-Review-App": "1"})
    assert code == 200 and r["ok"]
    saved = yaml.safe_load(server.read_text(encoding="utf-8"))
    assert saved["store"]["name"] == "우리치킨" and saved["reply"]["api_key"] == "AIza-secret"
    assert saved["reply"]["min_rating_to_auto_reply"] == 1
    code, s = _req("/api/settings")
    assert s["api_key_set"] is True and "AIza-secret" not in json.dumps(s)
    # 빈 키를 보내면 기존 키를 지우지 않음
    _req("/api/settings", {"api_key": ""}, {"X-Review-App": "1"})
    assert yaml.safe_load(server.read_text(encoding="utf-8"))["reply"]["api_key"] == "AIza-secret"
