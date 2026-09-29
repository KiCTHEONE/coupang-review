# 쿠팡이츠 리뷰 자동 답글

쿠팡이츠 사장님 사이트(store.coupangeats.com)의 리뷰 관리 화면에서 **답글이 없는 리뷰를 찾아 Claude로 답글을 작성하고 자동 등록**하는 도구입니다.
쿠팡이츠는 사장님용 공개 리뷰 API를 제공하지 않으므로 Playwright로 실제 브라우저를 조작합니다.

## 동작 방식

1. 저장된 브라우저 세션(또는 아이디/비밀번호)으로 사장님 사이트에 로그인합니다.
2. 리뷰 관리 페이지에서 리뷰를 읽고, 이미 답글이 있거나 이전에 처리한 리뷰(`state.json`)는 건너뜁니다.
3. 기준 별점(기본 3점) 미만인 리뷰는 **자동 등록하지 않고** 사장님이 직접 확인하도록 남겨둡니다.
4. Claude가 리뷰 내용, 별점, 메뉴를 보고 가게 말투에 맞는 답글을 씁니다. API 키가 없거나 호출이 실패하면 템플릿 답글을 사용합니다.
5. `--post` 옵션을 줄 때만 실제로 등록합니다. 기본값은 dry-run(미리보기)입니다.

## 설치

```bash
pip install -r requirements.txt
playwright install chromium
cp config.example.yaml config.yaml   # 가게 이름, 말투, 서명 등을 수정
export ANTHROPIC_API_KEY=sk-ant-...  # Claude 답글 생성용 (없으면 템플릿 사용)
```

## 사용법

```bash
# 1) 최초 1회: 브라우저 창에서 직접 로그인 (휴대폰 인증 등 포함). 세션은 .browser-profile/ 에 저장됩니다.
python -m coupang_review login

# 2) 셀렉터 점검: 리뷰 페이지를 debug/ 에 저장하고, 인식한 리뷰를 출력합니다.
python -m coupang_review inspect

# 3) 미리보기: 답글을 생성해 출력만 합니다 (등록 안 함).
python -m coupang_review run

# 4) 실제 등록
python -m coupang_review run --post
```

- `--headful`을 주면 브라우저 창을 띄워 진행 과정을 볼 수 있습니다.
- 환경변수 `COUPANG_EATS_ID` / `COUPANG_EATS_PW`를 설정하면 세션이 만료됐을 때 자동으로 로그인합니다. 추가 인증이 뜨면 `login` 명령을 다시 실행하세요.
- `run.interval_minutes`를 설정(예: 60)하면 주기적으로 반복 실행합니다. cron으로 `run --post`를 돌려도 됩니다.

## 셀렉터 조정 (중요)

사장님 사이트의 HTML 구조는 공개되어 있지 않고 수시로 바뀝니다. 기본 셀렉터(`coupang_review/config.py`의 `DEFAULTS.site.selectors`)는 **추정값**이므로, 처음 사용할 때 반드시 다음 순서로 확인하세요.

1. `python -m coupang_review inspect`를 실행한 뒤 `debug/reviews.html`과 `debug/reviews.png`를 확인합니다.
2. 리뷰 카드, 작성자, 별점, 내용, "사장님 댓글 등록" 버튼, 입력창, 등록 버튼의 셀렉터를 `config.yaml`의 `site.selectors`에 덮어씁니다.
3. `inspect`의 출력에 작성자, 별점, 답글 여부가 제대로 나오는지 확인한 뒤 dry-run → `--post` 순서로 진행합니다.

## 주의사항

- 자동화 도구 사용은 쿠팡이츠 이용약관에 저촉될 수 있습니다. 본인 매장 계정에서만, 과도하지 않은 빈도(`delay_between_replies_sec`, `max_replies_per_run`)로 사용하세요.
- `config.yaml`, `.browser-profile/`(로그인 세션), `state.json`은 `.gitignore`에 포함되어 있습니다. 커밋하지 마세요.
- 1~2점 리뷰에는 사장님이 직접 답글을 다는 편이 좋습니다. 필요하면 `min_rating_to_auto_reply`를 1로 낮출 수 있습니다.

## 테스트

```bash
pytest -q
```

`tests/fake_reviews.html`로 만든 가짜 리뷰 페이지에서 수집과 답글 등록 흐름을 검증합니다.
