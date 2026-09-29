"""브라우저 실행.

쿠팡이츠는 자동화 도구가 띄운 브라우저의 로그인을 403으로 막는다.
그래서 컴퓨터에 설치된 크롬/엣지를 평범한 프로그램처럼 실행하고(원격 디버깅 포트만 열어 둠),
Playwright는 그 브라우저에 나중에 연결(connect_over_cdp)해서 조작한다.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger(__name__)


def find_browser(cfg: dict) -> str | None:
    """설치된 크롬(없으면 엣지) 실행 파일 경로."""
    custom = cfg["run"].get("browser_executable")
    if custom:
        return custom
    candidates: list[str] = []
    if sys.platform == "win32":
        for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")):
            if base:
                candidates.append(os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"))
        for base in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES")):
            if base:
                candidates.append(os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"))
    elif sys.platform == "darwin":
        candidates += [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
    else:
        candidates += [shutil.which(n) or "" for n in ("google-chrome", "google-chrome-stable", "chromium", "microsoft-edge")]
    return next((c for c in candidates if c and os.path.exists(c)), None)


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def start_browser(cfg: dict, url: str, headless: bool = False) -> subprocess.Popen | None:
    """설치된 브라우저를 디버깅 포트를 열어 실행한다. 이미 떠 있으면 None."""
    port = int(cfg["run"]["cdp_port"])
    if _port_open(port):
        log.info("이미 실행 중인 브라우저에 연결합니다 (포트 %d)", port)
        return None
    exe = find_browser(cfg)
    if not exe:
        raise RuntimeError(
            "크롬 또는 엣지를 찾지 못했습니다. 크롬을 설치하거나 config.yaml 의 run.browser_executable 에 경로를 적어주세요."
        )
    profile = Path(cfg["run"]["chrome_profile_dir"]).resolve()
    profile.mkdir(parents=True, exist_ok=True)
    args = [
        exe,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--lang=ko-KR",
        "--window-size=1400,1000",
    ]
    if headless:
        args.append("--headless=new")
    args += list(cfg["run"].get("browser_args") or [])
    args.append(url)
    log.info("브라우저 실행: %s", exe)
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        if _port_open(port):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1).read()
                return proc
            except OSError:
                pass
        time.sleep(0.5)
    proc.terminate()
    raise RuntimeError("브라우저에 연결하지 못했습니다. 열려 있는 자동화용 크롬 창을 모두 닫고 다시 시도하세요.")


def _load_cookies(cfg: dict) -> list[dict]:
    path = Path(cfg["run"]["session_file"])
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("cookies", [])


@contextmanager
def open_context(p, cfg: dict, headless: bool, url: str = "about:blank"):
    """조작할 BrowserContext를 연다. run.browser_mode: real(설치된 브라우저, 기본) / bundled."""
    if cfg["run"].get("browser_mode", "real") == "bundled":
        ctx = p.chromium.launch_persistent_context(
            cfg["run"]["profile_dir"],
            headless=headless,
            executable_path=cfg["run"].get("browser_executable") or None,
            locale="ko-KR",
            viewport={"width": 1400, "height": 1000},
            ignore_default_args=["--enable-automation"],
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            yield ctx
        finally:
            ctx.close()
        return

    proc = start_browser(cfg, url, headless=headless)
    browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{cfg['run']['cdp_port']}")
    try:
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        cookies = _load_cookies(cfg)
        if cookies:
            ctx.add_cookies(cookies)
        yield ctx
    finally:
        browser.close()
        if proc:
            proc.terminate()


def save_session(ctx, cfg: dict) -> None:
    """로그인 쿠키(세션 쿠키 포함)를 파일로 저장. 브라우저를 껐다 켜도 로그인 유지."""
    ctx.storage_state(path=cfg["run"]["session_file"])
