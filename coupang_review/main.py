"""CLI 진입점.

  python -m coupang_review login     # 브라우저 창에서 직접 로그인 (세션 저장)
  python -m coupang_review inspect   # 리뷰 페이지 HTML/스크린샷 저장 + 인식된 리뷰 출력 (셀렉터 점검용)
  python -m coupang_review run       # 미답변 리뷰에 답글 생성/등록
"""
from __future__ import annotations

import argparse
import logging
import time

from playwright.sync_api import sync_playwright

from .config import load_config
from .generator import ReplyGenerator
from .site import CoupangEatsStore
from .state import ReplyState

log = logging.getLogger("coupang_review")


def _launch(p, cfg: dict, headless: bool):
    return p.chromium.launch_persistent_context(
        cfg["run"]["profile_dir"],
        headless=headless,
        locale="ko-KR",
        viewport={"width": 1400, "height": 1000},
    )


def cmd_login(cfg: dict) -> None:
    with sync_playwright() as p:
        ctx = _launch(p, cfg, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(cfg["site"]["login_url"])
        input("브라우저에서 로그인(추가 인증 포함)을 마친 뒤 Enter를 누르세요... ")
        ctx.close()
    print("로그인 세션이 저장되었습니다:", cfg["run"]["profile_dir"])


def cmd_inspect(cfg: dict) -> None:
    with sync_playwright() as p:
        ctx = _launch(p, cfg, headless=cfg["run"]["headless"])
        store = CoupangEatsStore(ctx, cfg["site"])
        store.ensure_login()
        store.open_reviews()
        out = store.dump()
        items = store.review_items()
        print(f"저장 위치: {out}/  |  인식된 리뷰 수: {len(items)}")
        for item in items[:10]:
            r = store.parse_review(item)
            print(f"- [{r.key}] {r.author} ★{r.rating} 답글={'O' if r.has_reply else 'X'} | {r.menu} | {r.text[:60]!r}")
        ctx.close()


def run_once(cfg: dict, dry_run: bool) -> int:
    run_cfg = cfg["run"]
    min_rating = int(cfg["reply"]["min_rating_to_auto_reply"])
    state = ReplyState(run_cfg["state_file"])
    generator = ReplyGenerator(cfg["store"], cfg["reply"])
    posted = 0
    seen: set[str] = set()

    with sync_playwright() as p:
        ctx = _launch(p, cfg, headless=run_cfg["headless"])
        store = CoupangEatsStore(ctx, cfg["site"])
        store.ensure_login()
        store.open_reviews()

        for page_no in range(1, int(run_cfg["max_pages"]) + 1):
            log.info("리뷰 %d페이지 처리", page_no)
            # 답글 등록 후 목록이 다시 그려질 수 있어, 한 건 처리할 때마다 목록을 새로 읽는다.
            while posted < int(run_cfg["max_replies_per_run"]):
                target = None
                for item in store.review_items():
                    review = store.parse_review(item)
                    if review.key in seen:
                        continue
                    seen.add(review.key)
                    if review.has_reply or state.has(review.key):
                        continue
                    if not review.text and review.rating is None:
                        log.warning("리뷰 내용을 읽지 못했습니다. 셀렉터를 확인하세요 (inspect 명령).")
                        continue
                    if review.rating is not None and review.rating < min_rating:
                        log.info("별점 %d점 리뷰는 직접 답글을 권장해 건너뜁니다: %s", review.rating, review.text[:40])
                        continue
                    target = (item, review)
                    break
                if target is None:
                    break

                item, review = target
                reply = generator.generate(review)
                print(f"\n[리뷰] {review.author} ★{review.rating} | {review.menu}\n  {review.text}\n[답글]\n  {reply}")
                if dry_run:
                    continue
                if store.post_reply(item, reply):
                    state.add(review.key, reply, author=review.author, rating=review.rating)
                    posted += 1
                    log.info("답글 등록 완료 (%d)", posted)
                else:
                    log.error("답글 등록을 확인하지 못했습니다: %s", review.key)
                time.sleep(float(run_cfg["delay_between_replies_sec"]))

            if posted >= int(run_cfg["max_replies_per_run"]) or not store.next_page():
                break
        ctx.close()
    return posted


def main() -> None:
    parser = argparse.ArgumentParser(prog="coupang_review", description="쿠팡이츠 리뷰 자동 답글")
    parser.add_argument("command", choices=["login", "inspect", "run"])
    parser.add_argument("-c", "--config", default="config.yaml")
    parser.add_argument("--post", action="store_true", help="실제로 답글 등록 (config의 dry_run 무시)")
    parser.add_argument("--dry-run", action="store_true", help="답글 생성만 하고 등록하지 않음")
    parser.add_argument("--headful", action="store_true", help="브라우저 창을 띄워서 실행")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

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
