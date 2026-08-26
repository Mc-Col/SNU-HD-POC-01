# 인수인계 — 서경빈 (스키마 · 텍스트 파서 · 형식 검증)

작성 2026-08-26 · 발표 2026-08-27 · 기준 커밋 `68e9452`

**이 문서 하나로 내 작업을 이어받을 수 있게 쓴다.** 무엇을 만들었는지, 숫자가
얼마인지, **무엇을 되돌리면 안 되는지**, 다음에 무엇을 할지 순서로 적는다.

관련 문서 — 공통 규칙은 `CLAUDE.md`, 결정 근거는 `docs/ARCHITECTURE.md`,
평가 이력은 `docs/eval_history.md`, 남은 결정은 `docs/TODO.md`.

---

## 0. 담당 범위 — 소유 파일

| 영역 | 경로 | 규모 |
|---|---|---|
| 스키마 3파일 | `schema/fields.yaml` · `rules.yaml` · `output_columns.yaml` | 규칙 사전 |
| ③-a 텍스트 파서 | `src/parsers/text/` | 본체 1,243줄 + 시험 1,235줄 |
| ⑤-a 형식 검증 | `src/validate/format/` | 본체 227줄 + 시험 253줄 |

**남의 모듈은 읽기만 한다**(철학 1). 예외로 승인받아 만진 곳이 셋 —
`src/pipeline.py`(조립), `src/triage/`(수정), `tools/gen_schema.py`.
승인 없이 다시 만지지 않는다.

**자기검증**

    python -m pytest -q                        전체 (2026-08-26 전건 통과)
    python -m pytest src/parsers/text -q       내 모듈만
    python -m src.pipeline --smoke             조립 확인
    python -m src.parsers.text.score_against_kit --kit readme/labeling_kit.xlsx --root raw_file

---

## 1. 만든 것 — 파일별

### 1-1 텍스트 파서 (`src/parsers/text/`)

| 파일 | 줄 | 하는 일 |
|---|---|---|
| `field_index.py` | 201 | 라벨 → 필드. **다중 등록 + 구역으로 고른다** |
| `sections.py` | 259 | **구역 인식** — 회전 글자 · 세로 병합 셀로 부품 블록을 찾는다 |
| `excel.py` | 293 | xlsx 판독. 병합 셀 · Nor 열 앵커 |
| `pdf_text.py` | 301 | 텍스트 PDF 판독. 열 허용오차 12pt |
| `crosscheck.py` | 208 | **VLM ↔ 텍스트 교차검증** 판정 6종 |
| `dual.py` | 157 | `DualParser` — 두 경로를 독립 실행하고 대조 |
| `adapter.py` | 83 | 파이프라인 계약(`RawExtraction`)으로 감싼다 |
| `units.py` · `xls_compat.py` | 95 | 단위 토큰 · 구형 xls |
| `score_against_kit.py` | 306 | 킷 대비 채점 (라벨러별 분리) |

**구역 인식이 이 모듈의 핵심 설계다.** 데이터시트는 같은 항목명을 부품마다
반복한다 — `52PV014` 한 장에 `Model`(밸브 본체) · `Model No.`(액추에이터) ·
`Model No. / Mfr.`(포지셔너)가 함께 있다. 이름만으로는 못 고른다.

문서가 답을 갖고 있다 — PDF 는 여백에 **90도 회전된 글자**로, 엑셀은 **세로로
병합된 셀**로 블록마다 이름표를 붙인다. 그것을 읽어 구역 경계를 세우고,
라벨이 어느 구역에 있는지로 필드를 고른다.

**두 가지 설계 판단 (되돌리지 말 것)**

1. **구역은 결합 키가 아니라 필터다.** `"VALVE BODY / BONNET Model"` 처럼
   합쳐 등록하지 않는다 — 구역 표기 변종만큼 사전이 배로 늘어난다.
   유사표현은 이름만 등록하고 **한 이름을 여러 필드에 등록**한 뒤 구역으로 고른다.
2. **사전에 등록된 이름만 구역으로 인정한다.** `10PV018` 정비보고서에는
   `Authorized by` 같은 세로 병합 셀이 5개 있다. 사전에 없으니 무시된다 — 오탐 방지다.

**구역 경계는 인접 표식의 중점**이고, 바깥쪽 경계는 반대쪽을 **거울로 접어**
정한다(`sections.py` 의 `own` / `lo` / `hi`). 문서 끝에서 블록이 잘리지 않게 하는 장치다.

    구역 표기 36개 → 표준 구역 8개
    general · service · body · trim · material · actuator · positioner · none

`none` 은 우리 28필드에 대응이 없는 구역(`SOLENOID V/V` · `LIMIT SW` · `NOTES` …)이다.
표준 구역으로 인정하되 **허용 필드를 비워** 그 안의 라벨이 값을 못 만들게 한다.

### 1-2 열 선택 — `band()`

**구역(부품)과 열(Min/Normal/Max)은 다른 축이다.** 처음에 구역이 같은지로 Nor
앵커를 제한했더니 `NORMAL FLOW RATE` 가 605(=Max)를 집었다. 정답은 553 이다.
그래서 **구역 키가 아니라 열 블록**(`SectionMap.band()`)으로 범위를 좁혔다.

이 구분이 왜 중요한지는 홀드아웃이 증명했다 — `docs/eval_history.md` 의
오류 30건 분류에서 **최다 원인이 "열 선택" 8건**이고 필드별 격차 상위 4개가
전부 공정 조건이다(`normal_flow_rate` 90%→20%). **VLM 경로에는 이 장치가 없다.**

### 1-3 형식 검증 (`src/validate/format/`)

| 규칙 | 판정 |
|---|---|
| `numeric` | 숫자가 있어야 하고 한글은 거부 |
| `tag` | `src.preprocess.parse_tag` 에 위임한다 (다시 만들지 않는다) |
| `max_length` | 필드마다 실측값 기준 |

28필드 전부에 `format_rules` 가 있다. `check_value()` 단일 필드 API 도 있어
화면·도구가 값 하나만 물어볼 수 있다.

### 1-4 교차검증 (`crosscheck.py` + `dual.py`)

**안 B 를 골랐다** — 두 경로를 **독립 실행하고 결과를 대조**한다.
안 A(텍스트를 VLM 프롬프트에 넣기)를 기각한 이유 셋 —

1. 파서 오류가 VLM 으로 **전파**된다
2. 모델이 넣어준 값에 **끌려간다**(anchoring)
3. **독립 측정을 잃는다** — 두 경로가 같은 답을 내는 것이 근거인데, 한쪽이
   다른 쪽을 봤으면 일치는 아무것도 뜻하지 않는다

    판정 6종  일치 · 표기차이 · 불일치 · VLM만 · 텍스트만 · 없음

**불일치면 `confidence=0.0`** 으로 낮춰 기존 `_decide()` 의 REVIEW 경로를 탄다.
새 상태를 만들지 않았다 — 화면·채점이 갈리지 않게 하려는 것이다.
**VLM 값을 덮어쓰지 않고**, 텍스트 파서가 죽어도 VLM 경로는 살아남는다.

---

## 2. 지금 숫자 (2026-08-26 · 병합 후 실측)

    채점 칸 315 · 정확 136 · 표기차이 60 · 정규화대기 52 · 오답 3 · 미추출 64
    파서 관점 성공률 79%   ← 맞는 칸을 집었는가. 파서 책임은 여기까지
    완전 일치율      62%   ← 표준값 변환까지 끝난 상태

경과 — 텍스트 파서는 **성공률 20% → 79%**, **오답 16 → 3** 으로 왔다.
`.tif` 는 이 모듈 범위가 아니다(VLM 담당). 채점 대상은 xlsx·텍스트PDF 13건이다.

### 오답 3건 — 전부 파서 잘못이 아닐 가능성이 있다

| 문서 | 필드 | 킷 정답 | 파서 | 판단 |
|---|---|---|---|---|
| d014 · d024 | POSITIONER MODEL NO. | `Logix 3800 Series` | `3821-28EA-D41L-0130-00` | **킷 확인 필요.** 파서 값이 오히려 모델번호 형식이다 |
| d040 | ENGINEERING TAG NO. | `10-LV-01073` | `10-LV-1073` | **킷 확인 필요.** 파일명이 `B10LV1073` 이다. 문서 안 표기를 봐야 한다 |

**이력** 킷 오류를 전에도 찾았다 — `d013` 포지셔너 모델 `880-2221` → `YT-1200`.
채점기가 킷을 검증하는 방향으로도 쓰인다.

---

## 3. 🔴 오늘 확인한 것 — 보고된 내 버그 2건은 이미 해결됐다

`docs/TODO.md` 의 **「전달할 것 — 서경빈 선임」** 항목을 실측으로 확인했다.

| 보고 내용 | 실측 결과 |
|---|---|
| `actuator_fail_action` 이 `Body Color` 를 집는다 (d005·d010) | **재현 안 됨.** 12건 전부 OPEN/CLOSE 계열을 정확히 집는다. `Body Color` 는 한 건도 없다 |
| 유사표현 누락 — `Body Size`(`4 IN`) · `Rating`(`ANSI CLASS 300`) | **별칭 있다.** `Body Size`→`valve_body_size`, `Rating`→`valve_body_rating`. 파서가 `ANSI CLASS 300` 을 뽑아낸다 |

보고 시점이 유사표현 166건 병합 이전이었을 것이다. **고칠 것이 아니라 닫을 항목이다.**

### 그리고 진짜 병목을 찾았다 — 규칙이 아니라 내 채점 도구다

정규화대기 **52칸을 전부 `DefaultNormalize` 에 넣어 봤다. 52칸 모두 변환된다.**

    ACTUATOR FAIL ACTION  12칸  'CLOSE' + 라벨 'Air Fails Valve to' → 'FAIL CLOSE'  ✅
    VALVE BODY RATING     12칸  'ANSI CLASS 300' → '300#'  ·  'ASME CL.600' → '600#' ✅
    CHARACTERISTIC        10칸  'EQ%' · 'EQ - %' → 'EQUAL PERCENTAGE'                ✅
    ACTUATOR TYPE          7칸  'PNEUMATIC DIAPHRAGM' · 'DIAPHRAGM - RA' → 'DIAPHRAGM' ✅
    VALVE LEAKAGE CLASS    3칸  'ANSI Class IV' → 'CLASS 4'                          ✅
    그 외                  8칸  치수·재질·Cv                                          ✅

**규칙 공백이 아니다.** `score_against_kit.py` 가 ④ Normalize 를 물리지 않아
숫자가 실제보다 낮게 나온다. 물리면 **완전 일치율 62% → 79%** 다(+17%p).
값이 아니라 **자를 고치는 일**이다. `docs/TODO.md` 가 요청한 방향과 같다 —
*"값 대조를 `eval/compare.py` 로 옮기면 로마자 정규화·깨진 단위 처리가 공짜로 따라온다."*

⚠️ **주의** 채점기가 Normalize 를 물면 **파서 결함이 규칙에 가려진다.**
그래서 두 숫자를 **둘 다** 낸다 — 파서 성공률(집었는가)과 완전 일치율(표준값까지).
하나로 합치면 어느 쪽을 고쳐야 하는지 알 수 없다.

---

## 4. 이번 병합에서 바뀐 것 — 내 결정이 뒤집힌 곳

### 4-1 표준형 변경 (수용한다)

| 필드 | 내가 정한 것 | 병합 후 | 왜 |
|---|---|---|---|
| `valve_body_rating` | `CLASS 300` | **`300#`** | 킷 정답이 `300#` 이 12건. 정답지에 맞춘 선택 |
| `valve_leakage_class` | `CLASS IV`(로마자) | **`CLASS 4`**(아라비아) | 같은 이유 |
| `manufacturer` | `FISHER` | `FISHER` (유지) | — |

내 근거(ANSI 는 폐기 명칭 · 규격 원문은 로마자)와 다르지만 **틀린 선택이 아니다.**
중요한 것은 표준형이 **하나로 정해지는 것**이고 이미 정해졌다. 되돌리지 않는다.
`docs/merge_handoff.md` §4 에 *"`valve_leakage_class`·`characteristic` 표준값은
내 쪽이 최신"* 으로 기록돼 있다.

### 4-2 내 스키마 결함 1건 — 이미 고쳐졌다 (분수 치수)

`1/2"` 가 `12"` 로 보정되고 있었다. **반 인치가 열두 인치가 된다.**
원인은 `src/schema.py` 의 `norm_label` 이 영숫자만 남겨 `/` 가 지워지는 것.
이종수 책임이 필드별 구두점 정책으로 고쳤다 —

    schema/rules.yaml   punct_significant: [valve_body_size]
    src/schema.py       norm_alias(값, 필드)  — 그 필드만 / . - 를 살려 대조

**표시가 붙지 않는 종류의 오류였다.** 별칭에 맞았으니 시스템은 정상 보정으로
처리하고 어휘 검증도 통과한다. 커밋 `67de2ae` 의 *"정답 대조 73→83%"* 에 이
오보정이 섞여 있을 수 있다. **앞으로 별칭을 추가할 때 구두점이 뜻을 바꾸는지 먼저 본다.**

### 4-3 확장된 것

    value_aliases   5필드 → 17필드 · 74규칙   (characteristic · valve_body_type
                                              · actuator_type · fluid_state 추가)
    derived_fields  type_name (태그 접두에서 도출)
    유사표현        166건 · 27/28 필드        (내 것 그대로)
    format_rules    28필드                    (내 것 그대로)
    sections        36표기 → 8구역            (내 것 그대로)

---

## 5. 🔴 통합 결함 — 화면 경로가 교차검증을 건너뛴다 (전달 필요)

`src/ui/` 는 이종수 책임 소유라 **고치지 않고 보고한다.** 실측 근거를 붙인다.

### 5-1 데모 경로에 `DualParser` 가 꽂히지 않았다

`src/ui/screens.py:158-163` 이 세 갈래다.

    origin == "fixture"                 → source.from_fixture(...)
    origin == "vlm" and use_vlm()       → source.from_vlm(path, page=session.page())   ← 실물 데모
    else                                → source.from_pipeline(path, only_mvp=...)

`from_vlm()`(`source.py:218`)은 `VlmParser()` 를 **직접** 부르고 `DefaultNormalize`·
`_decide` 를 손으로 이어 붙인다. `build()` 를 쓰지 않으므로 **`DualParser` 가 없다.**
즉 **실물 문서 데모에서 텍스트 교차검증이 동작하지 않는다.**

**고칠 곳은 두 줄이다** (`src/ui/source.py:252`)

    parser, norm = VlmParser(), DefaultNormalize()
    ↓
    from src.parsers.text.dual import DualParser
    from src.parsers.text.adapter import TextParser
    parser, norm = DualParser(VlmParser(), TextParser()), DefaultNormalize()

`DualParser.extract(path, triage, fields)` 는 `VlmParser` 와 **서명이 같다.**
텍스트 파서가 실패해도 VLM 경로는 그대로 산다(설계상 보장).
그리고 `from_vlm` 이 만드는 `PageInfo(page=pg, selected=True)` 를 내 어댑터가
`triage.selected_page` 로 읽으므로 **사람이 고른 쪽이 두 파서에 같이 전달된다.**

### 5-2 `from_pipeline()` 은 사람이 고른 쪽을 버린다

    def from_pipeline(path, *, only_mvp=True, use_vlm=True)   ← page 인자가 없다
    Pipeline.run_document(self, path)                          ← page 인자가 없다

`session.page()` 가 전달되지 않는다. 이 경로에서는 항상 1페이지를 읽는다.
**홀드아웃 `d040` 이 46% → 93% 로 오른 원인이 정확히 쪽 지정 오류였다**
(`docs/eval_history.md`). 같은 종류의 사고가 이 경로에 남아 있다.

**두 가지 고치는 길**

    (가) from_pipeline(path, page=session.page()) 로 받아 triage 에 selected 를 심는다
    (나) Pipeline.run_document(path, page=None) 을 추가한다   ← 조립 담당과 협의 필요

(가)가 화면 안에서 끝나므로 발표 전에는 (가)를 권한다.

---

## 6. 다음 작업 — 우선순위

| 순 | 할 일 | 소유 | 효과 | 근거 |
|---|---|---|---|---|
| 1 | **채점기에 ④ Normalize 를 물린다** (두 숫자 병기 유지) | 내 것 | 완전 일치 62% → 79% | §3 |
| 2 | **화면에 `DualParser` 꽂기 요청** | 이종수 | 데모에서 교차검증이 살아난다 | §5-1 |
| 3 | **`from_pipeline` 쪽 전달 요청** | 이종수 | d040 류 사고 방지 | §5-2 |
| 4 | **열 선택 규칙을 VLM 쪽에 넘긴다** | 강민호 | 홀드아웃 최다 오류 8건 | §1-2 |
| 5 | 오답 3건 킷 대조 (`d014`·`d024`·`d040`) | 공동 | 오답 3 → 1 가능 | §2 |
| 6 | 미추출 `MANUFACTURER` 12칸 정책 | 이종수 결정 | §7-1 | |
| 7 | 스캔 전용 15건에 형식 검증을 태울 수 있는지 측정 | 내 것 | 교차검증 없는 구간의 유일한 무료 수단 | — |

**1번은 규칙을 안 바꾸고 숫자를 정확하게 만드는 일**이라 발표 전에 반드시 한다.
2·3번은 코드 두 줄이지만 **내 소유가 아니다.** 요청으로 처리한다.

---

## 7. 팀에 전달할 것 (아직 안 닫힌 항목)

### 7-1 `MANUFACTURER` 미추출 12칸 — 유추할 것인가

**문서 대부분에 제조사 칸이 아예 없다.** 머리글에 로고·상호로만 있거나 모델번호만 있다.
`schema/rules.yaml` 에 `model_to_manufacturer` 규칙이 **있는데 `src/` 어디서도
호출되지 않는다.** 결정이 필요하다 —

    (가) 모델번호로 유추한다     정확도는 오르지만 철학 4(근거 없는 값)와 부딪힌다
    (나) N/A + note 로 남긴다   지금 동작. 미추출 12칸이 그대로 남는다
    (다) 유추하되 표시를 붙인다  `state=REVIEW` + note "모델번호에서 유추"

**(다)를 권한다.** 값은 쓸 수 있게 하고 사람이 확인하게 한다. 마스터에 들어갈 때
"읽은 값"과 "유추한 값"이 구별된다 — 이 과제가 없애려는 문제가 그 구별의 부재다.

### 7-2 `Positioner Type` 별칭은 채택하지 않는다 (측정으로 기각)

별칭을 넣었더니 **오답이 1 → 6** 으로 늘었다. `Type` 이 너무 흔해 다른 구역의
값을 끌어온다. **VLM 프롬프트 힌트로만 쓸 것**을 권한다.

### 7-3 `unit_only` 형식 규칙은 넣지 않는다 (측정으로 기각)

*"단위만 있고 숫자가 없으면 오류"* 규칙은 **정답에서 오탐 3건**을 냈다 —
`30 m3h` · `205 kg/cm2(g)` · `8 g/cm2G`. 측정 기록을 `rules.yaml` 에 남겼다.

### 7-4 골든셋 중복 행 — `d006` / `d029`

킷에 같은 문서가 두 번 있다. 표본 수가 부풀어 보인다.

### 7-5 Triage 잔여 패턴 3건

`REPORT_HEADER_KEYWORDS` · `SPEC_ITEM_KEYWORDS` · `MIN_SPEC_ITEMS_FOR_REPORT=2` 로
정비보고서 표지를 사양표로 오판하는 것을 막았다(승인받아 수정 · 머지 완료).
표지는 사양 항목이 0개, 실제 사양 쪽은 2개 이상이라는 실측에 기댄다.
**잔여 패턴 3건**은 강민호 책임 쪽에서 확인이 필요하다.

### 7-6 `eval/compare` 에 `Nm3` 가 없다

단위 대조에서 `Nm3` 계열이 빠져 있다. 유량 필드에서 표기차이가 오답으로 잡힌다.

---

## 8. 되돌리지 말 것 — 측정으로 기각된 것들

| 시도 | 결과 | 기록 |
|---|---|---|
| `Positioner Type` 별칭 | 오답 1 → 6 | §7-2 |
| `unit_only` 형식 규칙 | 정답에서 오탐 3건 | §7-3 |
| Nor 앵커를 **구역**으로 제한 | `NORMAL FLOW RATE` 553 → 605(Max) | §1-2 |
| 안 A — 텍스트를 VLM 프롬프트에 넣기 | 오류 전파 · anchoring · 독립 측정 상실 | §1-4 |
| 유사표현끼리 걸릴 때 필드 생성 | **표준명 소유 필드만 이긴다.** 아니면 만들지 않는다 | `field_index.py` |

`MERGE_ALIASES` 에 **중복 키가 있으면 별칭이 조용히 사라진다.**
`tools/gen_schema.py` 의 `assert_no_duplicate_alias_keys()` 가 ast 로 막는다.
이 가드를 빼지 않는다 — 실제로 별칭이 사라진 적이 있다.

---

## 9. 재현 명령 모음

    # 내 모듈
    python -m pytest src/parsers/text src/validate/format -q
    python -m src.parsers.text.score_against_kit --kit readme/labeling_kit.xlsx --root raw_file
    python -m src.parsers.text.report_crosscheck            # 두 경로 일치율
    python tools/refresh_text_fixtures.py                   # --write 없이 먼저 차이를 본다

    # 전체
    python -m pytest -q
    python -m src.pipeline --smoke
    python -m eval.harness --replay runs/raw/terra_x2       # API 비용 0원 재채점

    # 스키마
    python tools/gen_schema.py                              # 중복 별칭 가드 포함
    python tools/check_kit.py --kit readme/labeling_kit.xlsx

---

## 10. 발표에서 내 몫으로 말할 것

1. **텍스트 경로는 28% 구간의 무료 검산이다** — 텍스트PDF 16.6% + xlsx 11.6%.
   이 구간은 글자가 확실하므로 VLM 과 **독립으로** 읽어 대조할 수 있다.
   일치하면 근거가 두 개다. **API 비용은 0원이다.**
2. **커버리지를 숨기지 않는다** — 교차검증이 닿는 칸은 전체의 약 23% 다.
   나머지 스캔 구간(71.9%)에는 이 수단이 없다.
3. **열 선택이 최다 오류 원인이고, 텍스트 파서에는 그 장치가 있다**
   (`band()`). VLM 쪽에 넘길 규칙이다.
4. **측정으로 기각한 것을 함께 말한다** — 채택한 것만 말하면 숫자가 우연처럼 보인다.
