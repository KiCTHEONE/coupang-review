"""CLI 진입점.

  python -m coupang_review login     # 브라우저 창에서 직접 로그인 (세션 저장)
  python -m coupang_review inspect   # 리뷰 페이지 HTML/스크린샷 저장 + 인식된 리뷰 출력 (셀렉터 점검용)
  python -m coupang_review run       # 미답변 리뷰에 답글 생성/등록
"""
from __future__ import annotations

import argparse
import logging
import time
from contextlib import ExitStack

from playwright.sync_api import sync_playwright

from .browser import open_context, save_session, start_browser
from .config import load_config
from .generator import ReplyGenerator
from .models import AIUnavailable, QuotaExhausted, Review
from .site import CoupangEatsStore
from .state import ReplyState

log = logging.getLogger("coupang_review")
_RUN_ID = str(int(time.time()))


def login_start(cfg: dict):
    """로그인용 브라우저를 자동화 연결 없이 평범하게 띄운다 (쿠팡이츠 403 차단 회피)."""
    return start_browser(cfg, cfg["site"]["login_url"])


def login_finish(cfg: dict, proc) -> None:
    """로그인이 끝난 브라우저에 연결해 로그인 정보를 저장하고 닫는다."""
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{cfg['run']['cdp_port']}")
        save_session(browser.contexts[0], cfg)
        browser.close()
    if proc:
        proc.terminate()


def cmd_login(cfg: dict) -> None:
    proc = login_start(cfg)
    print("\n열린 브라우저 창에서 쿠팡이츠 사장님 사이트에 로그인하세요 (추가 인증 포함).")
    print("리뷰 관리 화면이 보이면 이 창으로 돌아와 Enter를 누르세요.")
    input("> ")
    login_finish(cfg, proc)
    print("로그인 정보를 저장했습니다:", cfg["run"]["session_file"])


def cmd_inspect(cfg: dict) -> None:
    with sync_playwright() as p, ExitStack() as stack:
        ctx = stack.enter_context(open_context(p, cfg, headless=cfg["run"]["headless"]))
        store = CoupangEatsStore(ctx, cfg["site"], reader=ReplyGenerator(cfg["store"], cfg["reply"]).read_card)
        store.ensure_login()
        store.open_reviews()
        out = store.dump()
        items = store.review_items()
        print(f"저장 위치: {out}/  |  인식된 리뷰 수: {len(items)}")
        for item in items[:10]:
            r = store.parse_review(item)
            if r is None:
                continue
            print(f"- [{r.key}] {r.author} ★{r.rating} 답글={'O' if r.has_reply else 'X'} | {r.menu} | {r.text[:60]!r}")


class Stopped(Exception):
    """사용자가 중지를 눌렀음."""


def run_once(cfg: dict, dry_run: bool, on_item=None, stop=None, only: dict[str, str] | None = None,
             only_meta: dict[str, dict] | None = None) -> int:
    """미답변 리뷰를 찾아 답글을 만들고(dry_run=False면 등록) 등록한 개수를 돌려준다.

    on_item: 리뷰 하나를 처리할 때마다 dict로 알려 받을 함수 (관리 화면용)
    stop: 설정되면 멈추는 threading.Event
    only: {리뷰키: 답글} — 주어지면 이 리뷰들만 해당 답글로 등록 (AI 호출 없음)
    only_meta: {리뷰키: {author, rating, menu, text}} — 등록 기록에 남길 리뷰 정보 (미리보기 때 읽은 것)
    """
    run_cfg = cfg["run"]
    state = ReplyState(run_cfg["state_file"])
    generator = ReplyGenerator(cfg["store"], cfg["reply"])
    seen: set[str] = set()
    counter = {"posted": 0}

    with sync_playwright() as p, open_context(p, cfg, headless=run_cfg["headless"]) as ctx:
        store = CoupangEatsStore(ctx, cfg["site"], reader=generator.read_card)
        store.ensure_login()
        store.open_reviews()
        try:
            _process(store, generator, state, cfg, dry_run, seen, counter, on_item or (lambda _: None), stop, only,
                     only_meta or {})
        except QuotaExhausted as e:
            log.warning("%s 여기서 멈춥니다. 이미 단 답글은 기록돼 있으니, 한도가 풀린 뒤(보통 다음 날) 다시 실행하면 이어서 처리합니다.", e)
        except Stopped:
            log.info("중지했습니다.")
        save_session(ctx, cfg)  # 갱신된 로그인 쿠키 저장
    log.info("확인한 리뷰 %d개", len(seen))
    return counter["posted"]


def _process(store, generator, state, cfg, dry_run, seen, counter, on_item, stop, only, only_meta) -> None:
    run_cfg = cfg["run"]
    min_rating = int(cfg["reply"]["min_rating_to_auto_reply"])
    max_pages = int(run_cfg["max_pages"]) or 1000
    max_replies = int(run_cfg["max_replies_per_run"]) or 10**9
    remaining = set(only) if only is not None else None

    def check_stop():
        if stop is not None and stop.is_set():
            raise Stopped()

    for page_no in range(1, max_pages + 1):
        log.info("리뷰 %d페이지 처리", page_no)
        more_loads = 0
        # 답글 등록 후 목록이 다시 그려질 수 있어, 한 건 처리할 때마다 목록을 새로 읽는다.
        while counter["posted"] < max_replies:
            check_stop()
            if remaining is not None and not remaining:
                return
            target = None
            for item in store.review_items():
                pre_key = store.item_key(item)
                if pre_key and (pre_key in seen or state.has(pre_key)):
                    continue
                if only is not None:
                    # 관리 화면에서 고른 리뷰만: AI를 부르지 않고 사장님이 확인한 답글을 그대로 등록
                    if not pre_key or pre_key not in only:
                        continue
                    seen.add(pre_key)
                    m = only_meta.get(pre_key, {})
                    review = Review(author=m.get("author", ""), rating=m.get("rating"), text=m.get("text", ""),
                                    menu=m.get("menu", ""), review_id=pre_key)
                    target = (item, review, only[pre_key])
                    break
                review = store.parse_review(item)
                if review is None:
                    seen.add(pre_key or "")
                    continue
                if review.key in seen:
                    continue
                seen.add(review.key)
                if review.has_reply or state.has(review.key):
                    continue
                if not review.text and review.rating is None:
                    log.warning("리뷰 내용을 읽지 못했습니다. 셀렉터를 확인하세요 (inspect 명령).")
                    continue
                low = review.rating is not None and review.rating < min_rating
                if low and not dry_run:
                    log.info("별점 %d점 리뷰는 직접 답글을 권장해 건너뜁니다: %s", review.rating, review.text[:40])
                    state.add(review.key, "", skipped="low_rating", author=review.author, rating=review.rating)
                    on_item(_item_dict(review, generator.finalize(review.ai_reply) if review.ai_reply else "", "low_rating"))
                    continue
                try:
                    reply = generator.finalize(review.ai_reply) if review.ai_reply.strip() else generator.generate(review)
                except AIUnavailable as e:
                    log.warning("답글을 만들지 못해 이 리뷰는 건너뜁니다 (다음 실행 때 다시 시도): %s", e)
                    on_item(_item_dict(review, "", "ai_failed"))
                    continue
                if dry_run:
                    print(f"\n[리뷰] {review.author} ★{review.rating} | {review.menu}\n  {review.text}\n[답글]\n  {reply}")
                    on_item(_item_dict(review, reply, "low_rating" if low else "preview"))
                    continue
                target = (item, review, reply)
                break
            if target is None:
                # 화면의 리뷰를 다 봤으면 '더보기'/스크롤로 더 불러온다.
                if more_loads < 500 and store.load_more():
                    more_loads += 1
                    continue
                break

            item, review, reply = target
            if remaining is not None:
                remaining.discard(review.key)
            print(f"\n[리뷰] {review.author} ★{review.rating} | {review.menu}\n  {review.text}\n[답글]\n  {reply}")
            if store.post_reply(item, reply):
                state.add(review.key, reply, author=review.author, rating=review.rating, text=review.text, run_id=_RUN_ID)
                counter["posted"] += 1
                log.info("답글 등록 완료 (%d)", counter["posted"])
                on_item(_item_dict(review, reply, "posted"))
            else:
                log.error("답글 등록을 확인하지 못했습니다: %s", review.key)
                on_item(_item_dict(review, reply, "post_failed"))
            time.sleep(float(run_cfg["delay_between_replies_sec"]))

        if counter["posted"] >= max_replies or not store.next_page():
            break
    if remaining:
        log.warning("화면에서 찾지 못한 리뷰 %d개는 등록하지 못했습니다 (이미 답글이 달렸거나 목록이 바뀜).", len(remaining))


def _item_dict(review: Review, reply: str, status: str) -> dict:
    return {
        "key": review.key, "author": review.author, "rating": review.rating, "menu": review.menu,
        "text": review.text, "reply": reply, "status": status,
    }


def make_shortcut() -> None:
    """바탕화면에 '쿠팡이츠 리뷰관리' 바로가기를 만든다 (Windows).
    서명된 pythonw.exe를 가리키므로 스마트 앱 컨트롤에 막히지 않고, 검은 창 없이 관리 화면이 열린다."""
    import base64
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    if sys.platform != "win32":
        print(f"Windows 전용입니다. 이 폴더({root})에서 'python -m coupang_review' 로 실행하세요.")
        return
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe") if exe.with_name("pythonw.exe").exists() else exe
    ps = f"""
$W = New-Object -ComObject WScript.Shell
$d = [Environment]::GetFolderPath('Desktop')
$s = $W.CreateShortcut((Join-Path $d '쿠팡이츠 리뷰관리.lnk'))
$s.TargetPath = '{pythonw}'
$s.Arguments = '-m coupang_review ui'
$s.WorkingDirectory = '{root}'
$s.IconLocation = '%SystemRoot%\\System32\\imageres.dll,76'
$s.Description = '쿠팡이츠 리뷰 답글 관리'
$s.Save()
Write-Output (Join-Path $d '쿠팡이츠 리뷰관리.lnk')
"""
    encoded = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
    out = subprocess.run(["powershell", "-NoProfile", "-EncodedCommand", encoded], capture_output=True, text=True)
    if out.returncode == 0:
        print("바탕화면에 바로가기를 만들었습니다:", out.stdout.strip())
        print("이제 '쿠팡이츠 리뷰관리' 아이콘을 더블클릭하면 관리 화면이 열립니다.")
    else:
        print("바로가기를 만들지 못했습니다:", out.stderr.strip())


def main() -> None:
    parser = argparse.ArgumentParser(prog="coupang_review", description="쿠팡이츠 리뷰 자동 답글")
    parser.add_argument("command", nargs="?", default="ui", choices=["ui", "shortcut", "login", "inspect", "run"],
                        help="ui(기본): 관리 화면 열기 / shortcut: 바탕화면 바로가기 만들기")
    parser.add_argument("-c", "--config", default="config.yaml")
    parser.add_argument("--post", action="store_true", help="실제로 답글 등록 (config의 dry_run 무시)")
    parser.add_argument("--dry-run", action="store_true", help="답글 생성만 하고 등록하지 않음")
    parser.add_argument("--headful", action="store_true", help="브라우저 창을 띄워서 실행")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    if args.command == "ui":
        from .ui import serve

        return serve(args.config)
    if args.command == "shortcut":
        return make_shortcut()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    cfg = load_config(args.config)
    if args.headful:
        cfg["run"]["headless"] = False

    if args.command == "login":
        return cmd_login(cfg)
    if args.command == "inspect":
        return cmd_inspect(cfg)

    dry_run = False if args.post else (True if args.dry_run else bool(cfg["run"]["dry_run"]))
    interval = float(cfg["run"]["interval_minutes"])
    if dry_run:
        log.info("DRY-RUN 모드: 답글을 생성만 하고 등록하지 않습니다. 실제 등록은 --post")
    while True:
        try:
            n = run_once(cfg, dry_run)
            log.info("이번 실행에서 등록한 답글: %d개", n)
        except Exception:
            if interval <= 0:
                raise
            log.exception("실행 중 오류, 다음 주기에 재시도합니다")
        if interval <= 0:
            break
        time.sleep(interval * 60)
