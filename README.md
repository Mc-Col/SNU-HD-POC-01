# D2S Ingestion Engine — PoC

**VLM 기반 연속공정 디지털트윈 Asset master agent 1단계 개발 PoC**

비정형 설비 문서에서 컨트롤밸브 기준정보 **28필드**를 추출·검증해 마스터 스키마 엑셀로
내보낸다. 사람이 최종 확정한다.

발표 **2026-08-27 (목)**

## 처음 오셨다면 — 5단계

| # | 할 일 | 참고 |
|---|---|---|
| 1 | GitHub Desktop 설치 후 이 저장소를 Clone | `docs/GIT_GUIDE.md` |
| 2 | **`setup.bat`** 더블클릭 | 자동 설치 |
| 3 | **`check_env.bat`** 더블클릭 → 전부 `OK` 확인 | 실패 시 화면에 조치 안내 |
| 4 | 문서 원본을 `raw_file/` 에 넣기 | 별도 전달 |
| 5 | `docs/roles/` 에서 **자기 이름 파일**을 읽기 | 내 역할 |

## 작업 시작

```
claude
```

Claude 에게:

```
docs/roles/<내 이름>.md 와 CLAUDE.md 와 docs/ARCHITECTURE.md 를 읽고
내 담당 모듈을 파악해줘.
fixtures 로 검증 가능한 최소 구현부터 시작하자.
```

## 먼저 읽을 것

| 문서 | 내용 |
|---|---|
| **`docs/ARCHITECTURE.md`** | **결정된 규칙 전부** — 태그·페이지 선택·범위·함정·코퍼스 실측 |
| `CLAUDE.md` | 개발 철학 6개, 절대 하지 말 것 |
| `docs/roles/<내 이름>.md` | 내 담당 모듈과 우선순위 |
| `docs/change_log.md` | 결정의 경과 (C001~) |
| `docs/insight_memory.md` | 판단의 배경 — 왜 그렇게 정했나 |

## 구조

```
schema/
  fields.yaml       28필드 정의 (기계 생성 — tools/gen_schema.py)   ← 서경빈 선임
  rules.yaml        단위·표기 변환 (손 관리)                        ← 서경빈 선임
  guidance.yaml     자연어 판단 지침 (화면에서도 편집)              ← 이종수·서경빈
src/
  contracts.py      계약 3개 + PageInfo                  ← 이종수 책임만 수정
  pipeline.py       하네스 · Loop A/B                    ← 이종수 책임만 수정
  hooks.py          훅 13개                              ← 이종수 책임만 수정
  schema.py         yaml 로더
  preprocess.py     공용 전처리 도구 (파일명·렌더·탐침·최신성)  ← 공용, 변경 시 요청
  triage/ router/   ① ②                                          ← 강민호 책임
  parsers/vlm/      ③-b                                          ← 강민호 책임
  parsers/text/     ③-a                                          ← 서경빈 선임
  normalize/        ④                                            ← 이종수 책임
  validate/format/  ⑤-a                                          ← 서경빈 선임
  validate/domain/  ⑤-b                                          ← 이종수 책임
  state/ ui/        ⑥ ⑦                                          ← 이종수 책임
eval/               평가 하네스 (홀드아웃 잠금 · --no-vlm)   ← 비어 있음
fixtures/           모듈별 입력·기대출력 + 자체 검증
tools/              gen_schema.py · gen_labelkit.py
docs/               ARCHITECTURE · PRINCIPLES · GIT_GUIDE · roles · 로그
runs/               실행 로그 (Git 제외)
raw_file/           문서 원본 (Git 제외)
```

## 지금 상태

**동작하는 것**

```
python -m src.pipeline --smoke          엔드투엔드 (기본 구현으로 끝까지 돈다)
python -m src.preprocess                전처리 도구 자체 확인
python fixtures/preprocess/test_tag.py       태그 규칙 51건
python fixtures/preprocess/test_recency.py   페이지 최신성 25건
streamlit run app.py                    HITL 화면
```

**비어 있는 것** — `src/triage` `src/router` `src/parsers/*` `src/normalize`
`src/validate/*` `src/state` `eval/`

`src/pipeline.py` 의 기본 구현이 끝까지 도는 경로를 제공하므로 모듈을 하나씩
끼워 넣으며 진행할 수 있다. 남의 모듈을 기다리지 않는다.

## 코퍼스 실측 (1,059건)

| 판정 | 건수 | 비율 |
|---|---|---|
| 대상 | 909 | 85.8% |
| 제외 (정비·개조 보고서 112 · 도면 13) | 125 | 11.8% |
| 판단 불가 → 내용 판정으로 | 25 | 2.4% |

대상 909건의 포맷: **`tif` 80.7%** · `pdf` 11.9% · `xlsx`·`xlsm` 7.4%

**VLM 이 주 경로다.** PDF 는 파일의 51.9% 가 텍스트·스캔 혼재여서 텍스트 레이어
판정은 파일 단위가 아니라 페이지 단위여야 한다.

## 골든셋 라벨링

`readme/labeling_kit.xlsx` — 30건. 배분:

| | 담당 | 폴더 |
|---|---|---|
| d001~d010 | 이종수 책임 | `raw_file/` 최상위 |
| 10건 | 강민호 책임 | `raw_file/강민호 책임님/` |
| 10건 | 서경빈 선임 | `raw_file/서경빈 선임님/` |

`raw_file/제외항목/` 30건은 대상이 아닌 문서(계기 목록·벤더 프린트·색인).

**소요 시간은 기록하지 않는다.** 표준만 정하면 라벨링은 금방이고, 실제 비용은
라벨링이 아니라 승인·반려 왕복에 있다.

## 규칙 요약

- **자기 모듈 폴더만 수정한다** — 계약·스키마·하네스는 소유자만
- 규칙은 코드가 아니라 `schema/*.yaml` 에
- 모르면 값을 만들지 않는다 (`state=NA` + `note`)
- 실패를 삼키지 않는다 (`on_error` 로 기록)
- 같은 입력 → 같은 출력 (난수·시각 금지)
- **`raw_file/` 과 `.env` 는 Git 에 올리지 않는다**

자세한 내용은 `CLAUDE.md` 와 `docs/PRINCIPLES.md`.

## 일정

| 날짜 | 내용 | |
|---|---|---|
| 8/21 (금) | 스키마 · 계약 · 라벨링 착수 | 완료 |
| 8/22~24 | 계약 · 하네스 · 전처리 도구 · 화면 · 규칙 확정 | 완료 |
| 8/24~25 | 골든셋 라벨링 완료 + 모듈 병렬 구현 | 진행 |
| 8/26 (수) | 통합 → 홀드아웃 평가 | |
| **8/27 (목)** | **발표** | |
