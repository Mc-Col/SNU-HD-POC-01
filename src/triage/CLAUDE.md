# src/triage — 강민호 책임

**① TRIAGE — 이 파일에 데이터가 있나, 어느 페이지를 볼 것인가**

- 입력: `파일 경로`
- 출력: `TriageResult`

## 먼저 — 도구는 이미 있다

`src/preprocess.py` 를 import 해서 쓴다. **다시 만들지 않는다.**

```python
from src import preprocess as pre

info  = pre.parse_filename(path)        # 태그·문서종류·rev
why   = pre.scope_reason(path)          # 제외 사유 (비어 있으면 대상)
pages = pre.probe_pages(path)           # 페이지별 텍스트 레이어
pngs  = pre.render_pages(path, out_dir) # 페이지 → PNG
grid  = pre.make_montage(pngs, out)     # 격자 (VLM 이진 판정용)
pick, reason = pre.pick_latest_spec(page_infos, file_tag)
```

이 모듈이 하는 일은 **도구를 조립해서 판정하고 `TriageResult` 를 채우는 것**이다.

## 할 일

1. `parse_filename()` → `file_tag` · `file_doc_kind` · `file_rev` 채우기.
   문서종류 97.3% 가 여기서 무료로 해결된다
2. `scope_reason()` 이 비어 있지 않으면 `out_of_scope` + 그 문구를 `reason` 에
3. `probe_pages()` → `PageInfo.has_text_layer` · `text_len`
4. `render_pages()` → `PageInfo.render_path`. **화면이 이 값을 쓴다**
5. `make_montage()` → VLM 이진 판정 1회로 `PageInfo.page_class`
6. 사양표 후보가 2장 이상이면 후보만 원본 해상도로 렌더 → 날짜(`parse_doc_date`)·
   표기(`find_marks`)·태그(`find_tags`) 읽어 `PageInfo` 채우기
7. `pick_latest_spec()` 으로 선택. `None` 이면 확인필요로 넘기기
8. 구조 통계는 `stats` 에

## 페이지 선택 — 최신성

```
① 폐기 표기(OLD·SUPERSEDED·VOID·폐기) 제외 — 사람이 이미 표시함
② 같은 설비의 다른 시점인가, 다른 설비인가 — 태그 교집합으로 가른다
③ 같은 설비 → 연도 최신 → 월·일 → RETROFIT/AS-BUILT/REVISED
④ 다른 설비 → 파일명 태그로 선택. 없으면 "자산 N건 발견"
⑤ 못 가리면 고르지 않는다
```

`pick_latest_spec()` 이 이 순서를 구현하고 사유 문구를 함께 돌려준다.
`fixtures/preprocess/test_recency.py` 30건이 이 규칙을 잠근다.

## 하지 말 것

- 예외를 던지지 말 것 — 미지원 포맷은 UNSUPPORTED 로 기록하고 정상 반환
- **태그 단독성으로 페이지를 고르지 말 것.** `10FV011` 은 사양표 2장 중
  단독 태그 쪽(p4)이 1986년 폐기본이다. 그걸 고르면 `MODEL NO.`(657-ED vs
  667-ED)와 `RATED CV`(70.7 vs 95)가 틀린다
- 못 가릴 때 아무거나 고르지 말 것 — `None` + 사유가 정답이다
- `out_of_scope` 판정 시 `reason` 을 비우지 말 것. 조용히 건너뛰면 처리 실패율과
  구분되지 않는다
- MVP 는 1파일 = 1자산 전제. `expected_tag_count` 는 1 로 둔다.
  여러 건이면 "자산 N건 발견" 만 띄우고 하나만 검출한다

## 규칙

- 이 폴더 밖의 파일을 수정하지 않는다 (`preprocess.py` 변경은 이종수 책임에게 요청)
- 계약은 `src/contracts.py` 를 import 해서 쓴다 (복사하지 않는다)
- 규칙·임계값은 `schema/*.yaml` 과 `preprocess` 의 상수에서 읽는다
- fixture: `fixtures/triage/`
