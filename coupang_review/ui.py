"""딸깍 관리 화면: 내 컴퓨터에서만 열리는 작은 웹 서버 + 페이지.

python -m coupang_review ui  → 브라우저에서 http://127.0.0.1:8765 이 열린다.
"""
from __future__ import annotations

import collections
import json
import logging
import os
import shutil
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

from .config import PERIODS, load_config
from .state import ReplyState

log = logging.getLogger("coupang_review")
ROOT = Path(__file__).resolve().parent.parent
PAGE = Path(__file__).with_name("ui.html")
PORT = 8765


class _LogBuffer(logging.Handler):
    def __init__(self, size: int = 400):
        super().__init__(logging.INFO)
        self.lines: collections.deque[str] = collections.deque(maxlen=size)
        self.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(("⚠️ " if record.levelno >= logging.WARNING else "") + self.format(record))


class App:
    """실행 작업(미리보기/등록/로그인)을 하나씩 백그라운드에서 돌린다."""

    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.lock = threading.Lock()
        self.job: str | None = None
        self.stop = threading.Event()
        self.items: dict[str, dict] = {}
        self.login_proc = None
        self.login_waiting = False
        self.logs = _LogBuffer()
        logging.getLogger("coupang_review").addHandler(self.logs)

    # ---------- 설정 ----------
    def cfg(self) -> dict:
        return load_config(self.config_path)

    def _raw_config(self) -> dict:
        if not self.config_path.exists():
            example = ROOT / "config.example.yaml"
            if example.exists():
                shutil.copy(example, self.config_path)
        if self.config_path.exists():
            return yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        return {}

    def get_settings(self) -> dict:
        cfg = self.cfg()
        return {
            "name": cfg["store"]["name"],
            "signature": cfg["store"]["signature"],
            "tone": cfg["store"]["tone"],
            "extra_instructions": cfg["store"]["extra_instructions"],
            "min_rating": int(cfg["reply"]["min_rating_to_auto_reply"]),
            "period": cfg["site"].get("period", "1주일"),
            "api_key_set": bool((cfg["reply"].get("api_key") or "").strip() or os.environ.get("GEMINI_API_KEY")),
        }

    def save_settings(self, data: dict) -> None:
        raw = self._raw_config()
        store = raw.setdefault("store", {})
        reply = raw.setdefault("reply", {})
        for k in ("name", "signature", "tone", "extra_instructions"):
            if k in data:
                store[k] = str(data[k]).strip()
        if "min_rating" in data:
            reply["min_rating_to_auto_reply"] = max(1, min(5, int(data["min_rating"])))
        if data.get("period") in PERIODS:
            raw.setdefault("site", {})["period"] = data["period"]
        if (data.get("api_key") or "").strip():
            reply["api_key"] = data["api_key"].strip()
        self.config_path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
        log.info("설정을 저장했습니다.")

    # ---------- 작업 ----------
    def _start(self, name: str, fn) -> str | None:
        with self.lock:
            if self.job:
                return f"'{self.job}' 작업이 진행 중입니다."
            self.job = name
            self.stop.clear()

        def runner():
            try:
                fn()
            except Exception as e:  # 화면에 오류를 보여준다
                log.error("오류: %s", e)
                logging.getLogger(__name__).debug("상세", exc_info=True)
            finally:
                self.job = None

        threading.Thread(target=runner, daemon=True).start()
        return None

    def _on_item(self, item: dict) -> None:
        old = self.items.get(item["key"], {})
        if item["status"] in ("posted", "post_failed") and old.get("text"):
            item = {**old, "reply": item["reply"] or old.get("reply", ""), "status": item["status"]}
        self.items[item["key"]] = item

    def run(self, mode: str, selected: dict[str, str] | None = None) -> str | None:
        from .main import run_once

        cfg = self.cfg()
        if mode == "preview":
            self.items = {}
            return self._start("미리보기", lambda: self._report(run_once(cfg, True, self._on_item, self.stop), preview=True))
        if mode == "post_all":
            return self._start("전체 자동 등록", lambda: self._report(run_once(cfg, False, self._on_item, self.stop)))
        if mode == "post_selected":
            if not selected:
                return "등록할 답글을 선택해 주세요."
            meta = {k: self.items[k] for k in selected if k in self.items}
            return self._start(
                "선택한 답글 등록",
                lambda: self._report(run_once(cfg, False, self._on_item, self.stop, only=selected, only_meta=meta)),
            )
        return "알 수 없는 작업입니다."

    def _report(self, posted: int, preview: bool = False) -> None:
        if preview:
            log.info("미리보기 완료: 답글 후보 %d개. 확인·수정한 뒤 '선택한 답글 등록'을 누르세요.",
                     sum(1 for i in self.items.values() if i["status"] in ("preview", "low_rating")))
        else:
            log.info("등록 완료: %d개", posted)

    def login_start(self) -> str | None:
        from .main import login_start

        def fn():
            self.login_proc = login_start(self.cfg())
            self.login_waiting = True
            log.info("열린 크롬 창에서 로그인한 뒤, 이 화면의 '로그인 완료'를 누르세요.")

        return self._start("로그인 창 열기", fn)

    def login_finish(self) -> str | None:
        from .main import login_finish

        if not self.login_waiting:
            return "먼저 '로그인'을 눌러 크롬 창을 여세요."

        def fn():
            login_finish(self.cfg(), self.login_proc)
            self.login_proc, self.login_waiting = None, False
            log.info("로그인 정보를 저장했습니다. 이제 '미리보기'를 눌러 보세요.")

        return self._start("로그인 저장", fn)

    def snapshot(self) -> dict:
        cfg = self.cfg()
        return {
            "job": self.job,
            "stopping": self.stop.is_set(),
            "logs": list(self.logs.lines)[-150:],
            "items": list(self.items.values()),
            "login_waiting": self.login_waiting,
            "has_session": Path(cfg["run"]["session_file"]).exists(),
        }

    def history(self) -> list[dict]:
        data = ReplyState(self.cfg()["run"]["state_file"]).data
        rows = [{"key": k, **v} for k, v in data.items() if v.get("reply")]
        return sorted(rows, key=lambda r: r.get("at", ""), reverse=True)[:300]


def _handler(app: App):
    allowed_hosts = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # 요청 로그는 숨김
            pass

        def _send(self, code: int, body, ctype="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _host_ok(self) -> bool:
            # 다른 사이트가 DNS 재바인딩으로 접근하는 것을 막는다
            return self.headers.get("Host", "") in allowed_hosts

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, {"error": "forbidden"})
            if self.path in ("/", "/index.html"):
                return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if self.path == "/api/state":
                return self._send(200, app.snapshot())
            if self.path == "/api/settings":
                return self._send(200, app.get_settings())
            if self.path == "/api/history":
                return self._send(200, app.history())
            self._send(404, {"error": "not found"})

        def do_POST(self):
            # 다른 사이트의 몰래 요청(CSRF)을 막기 위해 우리 페이지만 붙이는 헤더를 요구
            if not self._host_ok() or self.headers.get("X-Review-App") != "1":
                return self._send(403, {"error": "forbidden"})
            length = int(self.headers.get("Content-Length") or 0)
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._send(400, {"error": "잘못된 요청"})
            err = None
            if self.path == "/api/settings":
                try:
                    app.save_settings(data)
                except (ValueError, OSError) as e:
                    err = f"설정 저장 실패: {e}"
            elif self.path == "/api/run":
                err = app.run(data.get("mode", ""), data.get("selected"))
            elif self.path == "/api/stop":
                app.stop.set()
                log.info("중지 요청: 지금 처리 중인 리뷰까지만 하고 멈춥니다.")
            elif self.path == "/api/login/start":
                err = app.login_start()
            elif self.path == "/api/login/finish":
                err = app.login_finish()
            else:
                return self._send(404, {"error": "not found"})
            self._send(200 if not err else 409, {"ok": not err, "error": err})

    return Handler


def serve(config_path: str = "config.yaml", open_browser: bool = True) -> None:
    os.chdir(ROOT)  # 바로가기로 실행해도 설정/기록 파일을 이 폴더에서 찾도록
    if sys.stdout is None or sys.stderr is None:  # pythonw(창 없는 실행)일 때 출력은 파일로
        f = open(ROOT / "app.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or f
        sys.stderr = sys.stderr or f
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    for noisy in ("httpx", "google_genai", "google_genai.models"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    url = f"http://127.0.0.1:{PORT}"
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), _handler(App(ROOT / config_path)))
    except OSError:  # 이미 켜져 있으면 창만 연다
        webbrowser.open(url)
        return
    log.info("관리 화면: %s", url)
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    server.serve_forever()
