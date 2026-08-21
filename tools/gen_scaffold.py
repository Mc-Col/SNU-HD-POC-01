# -*- coding: utf-8 -*-
"""
프로젝트 골격 생성 — 폴더 · CLAUDE.md · 개인별 지시서 · 환경 스크립트

  python tools/gen_scaffold.py

이미 있는 파일은 덮어쓰지 않는다(--force 로 강제).
"""
import os, sys, io, textwrap
sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FORCE = "--force" in sys.argv
made, skipped = [], []


def W(rel, body, exe=False):
    p = os.path.join(ROOT, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if os.path.exists(p) and not FORCE:
        skipped.append(rel); return
    nl = "\r\n" if rel.endswith(".bat") else "\n"
    io.open(p, "w", encoding="utf-8", newline=nl).write(textwrap.dedent(body).lstrip("\n"))
    made.append(rel)


def D(rel):
    os.makedirs(os.path.join(ROOT, rel), exist_ok=True)


# ══════════════════════════════════════════════════════════════
#  모듈 정의 — (경로, 담당, 제목, 입력, 출력, 할 일, 하지 말 것)
# ══════════════════════════════════════════════════════════════
OWNER = {"lee": "이종수 책임", "kang": "강민호 책임", "seo": "서경빈 선임"}

MODULES = [
 ("src/triage", "kang", "① TRIAGE — 이 파일에 데이터가 있나",
  "파일 경로", "TriageResult",
  ["확장자·파일명 패턴 검사 (태그번호 정규식, schedule/summary/list 키워드)",
   "구조 통계 수집 (페이지 수, 텍스트 길이, 표·행 개수, 시트 수) → stats 에 담기",
   "첫 페이지·첫 시트 경량 판독으로 document_class 판정",
   "datasheet_embedded 의심 시 태그번호 출현 페이지 탐색 → targets 채우기",
   "out_of_scope 로 판정하면 reason 을 반드시 채운다"],
  ["예외를 던지지 말 것 — 미지원 포맷은 UNSUPPORTED 로 기록하고 정상 반환",
   "MVP 는 1파일 = 1자산 전제. expected_tag_count 는 1로 둔다"]),

 ("src/router", "kang", "② ROUTER — 어느 파서로 보낼지",
  "TriageResult + 파일", "ParserType + 처리 단위 목록",
  ["엑셀 계열 → 포맷별 리더 분기 (xlsx/xlsm 는 openpyxl, xls 는 xlrd)",
   "PDF 는 텍스트 레이어를 탐침 — 추출 가능 텍스트 비율이 임계 이상이면 PDF_TEXT, 아니면 VLM",
   "tif → 다중 페이지 분해 후 VLM",
   "판정 근거를 남긴다 (텍스트 비율 등)"],
  ["확장자만 보고 PDF 를 VLM 으로 보내지 말 것 — 텍스트 PDF 가 30% 다",
   "탐침 임계값은 하드코딩하지 말고 상수로 분리"]),

 ("src/parsers/vlm", "kang", "③-b VLM PARSER — 이미지에서 값과 위치를",
  "페이지 이미지 + 필드 정의", "RawExtraction[]",
  ["전처리 — 기울기 보정, 해상도 정규화",
   "schema/fields.yaml 의 name·desc·aliases 를 프롬프트에 주입",
   "값 + bbox + confidence 를 함께 요구한다 (bbox 가 UI 하이라이트의 근거)",
   "문서에 없으면 raw_value=None 으로 정직하게 반환",
   "재시도 요청 시 bbox 크롭만 재판독"],
  ["재시도 프롬프트에 '값이 틀렸다'고 쓰지 말 것 — 환각을 유도한다.",
   "  올바른 문구: '이 영역에 문자 그대로 무엇이 적혀 있는지 보고하라. 같은 값이면 같다고 답하라'",
   "없는 값을 추정해서 만들지 말 것"]),

 ("src/parsers/text", "seo", "③-a TEXT PARSER — 헤더를 표준 컬럼에 붙인다",
  "엑셀 시트 또는 텍스트 PDF 페이지", "RawExtraction[]",
  ["셀·텍스트 블록을 스캔해 라벨 후보 추출",
   "헤더 텍스트 → 표준 컬럼명 매핑 (schema/fields.yaml 의 name + aliases 사용)",
   "라벨 우측·하단 셀에서 값 추출 → source_locator 에 'Sheet1!C7' 형태로 기록",
   "raw_label 에 문서에 적힌 항목명을 그대로 담는다 ← 유사표현 사전이 여기서 자란다",
   "매핑 실패 라벨은 unmapped 로 수집해 로그에 남긴다"],
  ["셀 좌표로 매핑하지 말 것 — 벤더 양식의 디테일이 바뀐다. 헤더 텍스트 기준으로.",
   "aliases 를 코드에 넣지 말 것. schema/fields.yaml 에만 둔다"]),

 ("src/normalize", "lee", "④ NORMALIZE — 표준값으로 바꾼다",
  "RawExtraction[]", "표준값 + transform_trace",
  ["도메인 규칙 적용 — ATO → Fail Close, ATC → Fail Open (역전 매핑)",
   "변환 과정을 transform_trace 에 단계별로 기록",
   "schema/rules.yaml 을 읽어 적용 (코드에 규칙을 넣지 않는다)"],
  ["단위 변환은 MVP 범위 외 — 원문 표기 그대로 보존한다 (To-be)",
   "범주형 허용값 정규화도 MVP 범위 외"]),

 ("src/validate/format", "seo", "⑤-a 형식·허용값 검증",
  "정규화된 값", "판정 + 위반 사유",
  ["필수 필드 충족 검사 (fields.yaml 의 required)",
   "타입·형식 검사 (숫자여야 하는 필드에 문자가 왔는지 등)",
   "위반 시 FailureKind.FORMAT 으로 표시하고 사유를 남긴다"],
  ["위반을 발견하면 재시도를 요청하지 말 것 — 형식 위반은 사람에게 넘긴다",
   "범주형 허용값 리스트는 MVP 범위 외"]),

 ("src/validate/domain", "lee", "⑤-b 도메인·물리 제약 검증",
  "정규화된 값 세트", "판정 + 위반 사유",
  ["FAIL ACTION ↔ ACTUATOR TYPE 정합성 교차검증",
   "유효 ANSI 클래스 / 표준 배관 규격 검사",
   "위반 시 FailureKind.CONSTRAINT — 재시도하지 않고 사람에게"],
  ["Cv 물리 교차검증은 루프에서 제외 — UI 의 [Cv 계산] 버튼 기능으로 별도 구현",
   "제약 위반에 재시도를 걸지 말 것 (환각 제조)"]),

 ("src/state", "lee", "⑥ STATE — 셋 중 하나로 확정한다",
  "검증 결과", "FieldRecord",
  ["fields.yaml 의 threshold·safety 로 상태 결정 (AUTO / REVIEW / NA)",
   "note 를 생성한다 — AUTO 외 상태는 note 가 비면 계약 위반으로 예외 발생",
   "안전·식별 필드는 AUTO 여도 사람 확인 필요 표시",
   "FieldRecord.validate() 를 반드시 호출"],
  ["임계값을 코드에 하드코딩하지 말 것 — fields.yaml 에서 읽는다"]),

 ("src/ui", "lee", "⑦ HITL — 사람이 확정한다 (Streamlit)",
  "DocumentResult", "승인 레코드 + 엑셀",
  ["좌측 원본 이미지 / 우측 항목 표 (화면정의서 기준)",
   "확인필요 항목에 마우스 오버 시 bbox 영역 하이라이트",
   "정상추출은 패스, 확인필요·N/A 는 개별 확인 강제",
   "필수 필드가 모두 해소되면 '검토 완료' 활성화 (DocumentResult.approvable)",
   "[Cv 계산] 버튼 — RATED CV 가 N/A 일 때 공정조건으로 계산 제안",
   "엑셀 export"],
  ["검증 세션 전체 소요시간은 스톱워치로 수동 측정 (자동 로깅은 MVP 범위 외)"]),
]


# ── 루트 CLAUDE.md ────────────────────────────────────────────
W("CLAUDE.md", '''
# CLAUDE.md — 공통 컨텍스트

이 파일은 Claude Code 가 자동으로 읽는다. 작업 전 반드시 이 규칙을 따른다.

## 프로젝트

**VLM 기반 연속공정 디지털트윈 Asset master agent 1단계 개발 PoC**

비정형 설비 문서(엑셀 · 텍스트 PDF · 스캔 이미지)에서 컨트롤밸브 기준정보 30필드를
추출·검증해 마스터 스키마 엑셀로 내보낸다. 사람이 최종 확정한다.

- 발표: 2026-08-27
- 산출물 기준: `docs/ARCHITECTURE.md`
- 설계 판단 근거: `docs/insight_memory.md`

## 데이터 흐름

```
파일 ─▶ ① Triage ─▶ ② Router ─▶ ③ Parser(Text|VLM) ─▶ ④ Normalize
     ─▶ ⑤ Validate(형식→도메인→평가Agent) ─▶ ⑥ State ─▶ ⑦ HITL ─▶ 엑셀
```

계약은 `src/contracts.py` 세 개뿐이다: `TriageResult` → `RawExtraction` → `FieldRecord`.

## 개발 철학 — 6개

1. **계약 밖을 만지지 않는다.**
   자기 모듈 폴더만 수정한다. `src/contracts.py`, `schema/*.yaml`, `src/pipeline.py`,
   `src/hooks.py` 는 소유자만 수정한다. 변경이 필요하면 요청한다.
2. **규칙은 코드가 아니라 `schema/*.yaml` 에.**
   단위·표기·도메인 규칙을 파이썬에 하드코딩하지 않는다. 도메인 전문가가 검토할 수
   있어야 하고, PoC 가 중단되어도 남는 산출물이다.
3. **모든 모듈은 fixture 로 자기 검증이 가능해야 한다.**
   `fixtures/<모듈>/` 에 입력과 기대 출력을 둔다. 남의 모듈을 기다리지 않는다.
4. **근거 없는 값을 만들지 않는다.**
   모르면 `state=NA` + `note`. 추정값이 마스터DB 에 들어가면 이 과제가 해결하려는
   문제를 재생산한다.
5. **실패를 삼키지 않는다.**
   예외를 조용히 넘기지 않고 `on_error` 로 기록한다. 처리 실패율도 측정 대상이다.
6. **같은 입력 → 같은 출력.**
   난수·시각에 의존하지 않는다. 개선을 측정할 수 없게 된다.

## 절대 하지 말 것

- **`raw_file/` 을 Git 에 올리지 않는다.** 회사 문서 1,089건이다. `.gitignore` 에 있다.
- **`.env`(API 키)를 올리지 않는다.**
- **재시도 프롬프트에 "값이 틀렸다"고 쓰지 않는다.** 환각을 유도한다.
  올바른 문구: *"이 영역에 문자 그대로 무엇이 적혀 있는지 보고하라. 같은 값이면 같다고 답하라"*
- **제약 위반에 재시도를 걸지 않는다.** 재시도는 추출 실패(못 읽음)에만.

## 내 역할 찾기

`docs/roles/` 에서 자기 이름 파일을 읽는다. 자기 모듈 폴더의 `CLAUDE.md` 도 함께 읽는다.
''')

# ── PRINCIPLES / GIT_GUIDE ────────────────────────────────────
W("docs/PRINCIPLES.md", '''
# 개발 철학

## 왜 이 규칙들이 있는가

3명이 각자 Claude Code 로 개발한 뒤 합친다. 합칠 때 깨지지 않게 하는 것이 목적이다.
그리고 각 규칙에는 KPI 나 안전 속성이 하나씩 걸려 있다.

| 규칙 | 없으면 |
|---|---|
| 계약 밖을 만지지 않는다 | 병합 충돌. 코딩 에이전트가 남의 모듈을 "친절하게" 고친다 |
| 규칙은 `schema/*.yaml` 에 | 도메인 전문가가 검토 불가. 산출물이 코드에 묻힌다 |
| fixture 로 자기 검증 | 남의 모듈을 기다려야 한다. 에이전트가 스스로 반복 못 한다 |
| 근거 없는 값 금지 | 부정확한 기준정보를 재생산한다 |
| 실패를 삼키지 않는다 | 처리 실패율이 측정되지 않고, 누락 필드를 아무도 모른다 |
| 재현성 | 개선했는지 측정할 수 없다 |

## 재시도 규칙 — 가장 중요

```
추출 실패 (못 읽음 · 공백 · OCR 깨짐)  →  재시도 가능 (최대 2회)
제약 위반 (읽었으나 값이 이상)          →  재시도 금지. 경고 + note + 사람에게
문서에 근거 없음                        →  state=NA + note
```

"값이 제약을 위반했다, 다시 확인하라"는 프롬프트는 실질적으로 *다른 답을 내놓으라*는
지시다. 문서에 진짜로 그 값이 있으면 모델은 제약을 만족하는 값을 만들어 순응하고,
검증을 통과해 자동확정된다. 기준정보 시스템에서 최악의 실패 모드다.

## 상태 3분류

| 상태 | 의미 | 승인 |
|---|---|---|
| `AUTO` | 확신도 충족 + 검증 통과 | 일괄 승인 가능 (안전·식별 필드는 제외) |
| `REVIEW` | 확신도 미달 또는 검증 위반 | 개별 확인 필수. **note 필수** |
| `NA` | 문서에 근거 없음 | 사람이 채움. **note 필수** |

`FieldRecord.validate()` 가 note 누락을 예외로 잡는다.
''')

W("docs/GIT_GUIDE.md", '''
# Git 사용법 — 처음이어도 됩니다

명령어를 외울 필요 없습니다. **GitHub Desktop** 이라는 프로그램의 버튼만 누르면 됩니다.

## 1. 준비 (한 번만)

1. https://desktop.github.com 에서 **GitHub Desktop** 을 설치합니다.
2. GitHub 계정으로 로그인합니다. (초대 메일을 먼저 수락해 주세요)
3. `File` → `Clone repository` → 목록에서 이 프로젝트를 선택 → `Clone`
   - 저장 위치는 기본값 그대로 두면 됩니다.

## 2. 환경 설치 (한 번만)

프로젝트 폴더를 열고 **`setup.bat` 을 더블클릭**합니다.
검은 창이 뜨고 자동으로 설치됩니다. "설치 완료"가 나오면 닫으면 됩니다.

그다음 **`check_env.bat` 을 더블클릭**합니다.
모두 `OK` 로 나오면 준비가 끝났습니다. `실패`가 있으면 화면에 무엇을 하라고 적혀 있습니다.

## 3. 매일 작업 순서

### 시작할 때 — 다른 사람 작업 받아오기
GitHub Desktop 상단의 **`Fetch origin`** 을 누릅니다.
`Pull origin` 으로 바뀌면 한 번 더 누릅니다.

> 작업 시작 전에 이걸 먼저 하세요. 나중에 하면 충돌이 생길 수 있습니다.

### 작업하기
자기 모듈 폴더에서 Claude Code 를 실행합니다.

```
claude
```

`docs/roles/` 의 자기 이름 파일을 읽으라고 하면 알아서 파악합니다.

### 끝낼 때 — 내 작업 올리기
1. GitHub Desktop 왼쪽에 바뀐 파일 목록이 보입니다.
2. 왼쪽 아래 칸에 무엇을 했는지 한 줄 적습니다. (예: `텍스트 파서 헤더 매핑 구현`)
3. **`Commit to main`** 을 누릅니다.
4. 상단 **`Push origin`** 을 누릅니다.

## 4. 주의사항

### `raw_file` 폴더가 목록에 안 보이는 게 정상입니다
회사 문서 1,089건이라 올리면 안 됩니다. 일부러 제외해 두었습니다.
**강제로 올리려고 하지 마세요.**

`.env`(API 키), `runs/`(실행 로그)도 같은 이유로 안 보입니다.

### 문서 파일은 각자 로컬에 두세요
`raw_file/` 폴더를 프로젝트 폴더 안에 **같은 이름으로** 두면 됩니다.
별도로 전달드립니다.

### 빨간 글씨(Conflict)가 나오면
혼자 해결하지 마시고 이종수 책임에게 알려주세요. 대부분 30초면 해결됩니다.

## 5. 규칙 하나

**자기 모듈 폴더만 수정합니다.**
다른 사람 폴더나 `src/contracts.py`, `schema/*.yaml` 을 고쳐야 하면 소유자에게 말해주세요.
이 규칙만 지키면 충돌이 거의 생기지 않습니다.
''')

# ── 개인별 지시서 ─────────────────────────────────────────────
def role_doc(name, key, title, mods, extra):
    lines = [f"# {name} — 작업 지시서", "", f"**{title}**", "",
             "## 시작하는 방법", "",
             "```", "cd <프로젝트 폴더>", "claude", "```", "",
             "Claude 에게 이렇게 말하면 됩니다:", "",
             "```",
             f"docs/roles/{name}.md 와 CLAUDE.md 를 읽고 내 담당 모듈을 파악해줘.",
             "그다음 fixtures 로 검증 가능한 최소 구현부터 시작하자.",
             "```", "",
             "Claude 가 아키텍처·계약·규칙을 자동으로 읽고 파악합니다.", "",
             "## 담당 모듈", ""]
    for path, own, mt, inp, out, todo, dont in MODULES:
        if own != key:
            continue
        lines += [f"### `{path}`", "", f"**{mt}**", "",
                  f"- 입력: `{inp}`", f"- 출력: `{out}`", "", "할 일:", ""]
        lines += [f"{i+1}. {t}" for i, t in enumerate(todo)]
        lines += ["", "하지 말 것:", ""]
        lines += [f"- {d}" for d in dont]
        lines += [""]
    lines += ["## 완료 조건", "",
              "- `fixtures/<모듈>/` 의 입력으로 돌려서 기대 출력이 나온다",
              "- 계약(`src/contracts.py`)의 타입을 그대로 반환한다",
              "- 실패 시 예외를 삼키지 않고 기록한다", ""]
    lines += extra
    lines += ["", "## 막히면", "",
              "이종수 책임에게 말해주세요. 계약이나 공유 파일을 바꿔야 하는 경우가 대부분입니다.", ""]
    return "\n".join(lines)


W("docs/roles/이종수 책임.md", role_doc(
  "이종수 책임", "lee", "기획 · 도메인 — 하네스와 루프, 그리고 사람이 보는 화면",
  MODULES,
  ["## 추가 담당", "",
   "- `src/pipeline.py` — 전체 루프 제어, Loop A(재시도) 구현",
   "- `src/hooks.py` — Hook 12개 정의 및 로깅",
   "- `schema/rules.yaml` 의 도메인 규칙 부분 (서경빈 선임과 공동)",
   "- 계약 변경 승인 — 다른 사람이 요청하면 여기서 결정",
   "",
   "## 우선순위", "",
   "1. `hooks.py` + `pipeline.py` 골격 — 다른 사람이 붙일 자리가 생긴다",
   "2. `src/state` — 계약 ③을 완성시킨다",
   "3. `src/ui` — 발표 임팩트가 가장 큰 부분",
   "4. `src/normalize`, `src/validate/domain`"]))

W("docs/roles/강민호 책임.md", role_doc(
  "강민호 책임", "kang", "개발 · 도메인 — 비정형 입력을 다루는 계층",
  MODULES,
  ["## 우선순위", "",
   "1. `src/router` 의 **PDF 텍스트 레이어 탐침** — 이게 없으면 텍스트 PDF 30% 가 낭비된다",
   "2. `src/triage` — MVP 는 3-class 판정만 (datasheet / embedded / out_of_scope)",
   "3. `src/parsers/vlm` — bbox 를 반드시 함께 반환. UI 하이라이트의 근거다",
   "",
   "## 참고", "",
   "- 지원 포맷: `xlsx` `xlsm` `xls` `pdf` `tif`. `doc`/`docx`/`dwg` 는 범위 외",
   "- `xls` 는 `openpyxl` 로 못 읽는다 → `xlrd` 분기 필요",
   "- 스캔 이미지가 전체의 30%. 가장 어렵고 가장 중요한 경로다"]))

W("docs/roles/서경빈 선임.md", role_doc(
  "서경빈 선임", "seo", "데이터 표준 — 필드 정의와 헤더 매핑",
  MODULES,
  ["## 추가 담당 — 가장 먼저 필요한 것", "",
   "- `schema/fields.yaml` — 이미 생성되어 있음. 검토하고 보강",
   "  - 재생성: `python tools/gen_schema.py` (readme/output_sample.xlsx 가 원본)",
   "  - **유사표현이 9/30 필드만 채워져 있음** — 여기가 가장 큰 작업",
   "- `schema/rules.yaml` — 표기 매핑 사전",
   "",
   "## 우선순위", "",
   "1. **`fields.yaml` 의 유사표현 보강** — Text Parser 정확도가 여기서 결정된다",
   "2. `src/parsers/text` — 엑셀 우선 (전체의 40%)",
   "3. `src/validate/format`",
   "",
   "## 핵심", "",
   "`raw_label` 을 반드시 채워 주세요. 문서에 적힌 항목명을 그대로 담는 필드입니다.",
   "이 값이 쌓이면 유사표현 사전이 자동으로 자랍니다 — 30필드의 표기 변종을 사람이",
   "상상해서 채우는 것은 불가능하고, 실물에서 수집되어야 합니다."]))

# ── 모듈 폴더 + CLAUDE.md + __init__ ─────────────────────────
for path, own, title, inp, out, todo, dont in MODULES:
    D(path)
    body = [f"# {path} — {OWNER[own]}", "", f"**{title}**", "",
            f"- 입력: `{inp}`", f"- 출력: `{out}`", "", "## 할 일", ""]
    body += [f"{i+1}. {t}" for i, t in enumerate(todo)]
    body += ["", "## 하지 말 것", ""]
    body += [f"- {d}" for d in dont]
    body += ["", "## 규칙", "",
             "- 이 폴더 밖의 파일을 수정하지 않는다",
             "- 계약은 `src/contracts.py` 를 import 해서 쓴다 (복사하지 않는다)",
             "- 규칙·임계값은 `schema/*.yaml` 에서 읽는다",
             f"- fixture: `fixtures/{os.path.basename(path)}/`", ""]
    W(f"{path}/CLAUDE.md", "\n".join(body))
    W(f"{path}/__init__.py", '"""%s"""\n' % title)
    D(f"fixtures/{os.path.basename(path)}")

for d in ("eval", "runs", "schema", "fixtures"):
    D(d)

# ── rules.yaml 골격 ───────────────────────────────────────────
W("schema/rules.yaml", '''
# 정규화 · 변환 규칙표
#
# 소유: 서경빈 선임 (표기 매핑) / 이종수 책임 (도메인 규칙)
# 코드에 규칙을 넣지 말고 이 파일에 추가한다.

meta:
  version: "0.1.0"
  note: "MVP — 단위 변환과 범주형 허용값은 범위 외(To-be)"

# ── 도메인 규칙 ────────────────────────────────────────────────
# 문서 표기가 표준값과 다른 의미를 갖는 경우. 역전 매핑 주의.
domain_rules:
  actuator_fail_action:
    description: "데이터시트는 공기 작용 방향(ATO/ATC)으로 기재하고, 표준값은 공기 상실 시 거동(Fail)이다. 서로 반대다."
    map:
      - from: ["AIR TO OPEN", "AIR-TO-OPEN", "ATO", "A/O"]
        to: "FAIL CLOSE"
        trace: "ATO → 공기 상실 시 스프링 폐쇄 → Fail Close"
      - from: ["AIR TO CLOSE", "AIR-TO-CLOSE", "ATC", "A/C"]
        to: "FAIL OPEN"
        trace: "ATC → 공기 상실 시 스프링 개방 → Fail Open"
      - from: ["FAIL LAST", "FAIL IN PLACE", "FAIL LOCK"]
        to: "FAIL LAST"
        trace: "직접 기재"
    cross_check:
      - "ACTUATOR TYPE 이 Pneumatic 계열인지 확인"

# ── 표기 매핑 ──────────────────────────────────────────────────
# 같은 값의 다른 표기. 라벨링 중 raw_label 로 수집되는 것을 여기에 누적한다.
value_aliases: {}
  # 예:
  # valve_body_material:
  #   - from: ["CS", "C.S.", "CARBON STEEL"]
  #     to: "CARBON STEEL"

# ── To-be (MVP 범위 외) ───────────────────────────────────────
unit_conversion:
  enabled: false
  note: "MVP 는 원문 표기 보존. 향후 분류별 단위를 고정하고 변환까지 구현"

enum_allowed_values:
  enabled: false
  note: "범주형 허용값 리스트. MVP 범위 외"
''')

# ── 환경 ─────────────────────────────────────────────────────
W("requirements.txt", '''
# 공통 환경 — setup.bat 이 자동 설치합니다
openpyxl>=3.1
xlrd>=2.0
pymupdf>=1.24
pyyaml>=6.0
pillow>=10.0
pandas>=2.0
streamlit>=1.36
anthropic>=0.40
python-dotenv>=1.0
pytest>=8.0
''')

W("setup.bat", r'''
@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo ============================================
echo   D2S PoC - 개발 환경 설치
echo ============================================
echo.

where python > nul 2>&1
if errorlevel 1 (
  echo [실패] Python 이 설치되어 있지 않습니다.
  echo.
  echo   https://www.python.org/downloads/ 에서 설치한 뒤
  echo   설치 화면에서 "Add Python to PATH" 를 반드시 체크하세요.
  echo.
  pause
  exit /b 1
)

echo [1/3] 가상환경 생성...
if not exist ".venv" (
  python -m venv .venv
) else (
  echo       이미 있습니다. 넘어갑니다.
)

echo [2/3] 패키지 설치... (몇 분 걸립니다)
call .venv\Scripts\python.exe -m pip install --upgrade pip --quiet
call .venv\Scripts\python.exe -m pip install -r requirements.txt --quiet
if errorlevel 1 (
  echo [실패] 패키지 설치에 실패했습니다. 인터넷 연결을 확인하세요.
  pause
  exit /b 1
)

echo [3/3] API 키 파일 준비...
if not exist ".env" (
  echo ANTHROPIC_API_KEY=여기에_키를_붙여넣으세요 > .env
  echo       .env 파일을 만들었습니다. 메모장으로 열어 키를 넣어주세요.
) else (
  echo       이미 있습니다.
)

echo.
echo ============================================
echo   설치 완료
echo   이제 check_env.bat 을 더블클릭하세요.
echo ============================================
pause
''')

W("check_env.bat", r'''
@echo off
chcp 65001 > nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [실패] 가상환경이 없습니다. setup.bat 을 먼저 실행하세요.
  pause
  exit /b 1
)
call .venv\Scripts\python.exe tools\check_env.py
pause
''')

W("tools/check_env.py", '''
# -*- coding: utf-8 -*-
"""환경 자체 진단 — 무엇이 문제이고 무엇을 해야 하는지 직접 알려준다."""
import importlib, os, sys
sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ok = True

def chk(label, cond, fix=""):
    global ok
    if cond:
        print(f"  OK    {label}")
    else:
        ok = False
        print(f"  실패  {label}")
        if fix:
            print(f"        → {fix}")

print("=" * 52)
print("  D2S PoC - 환경 진단")
print("=" * 52)

print("\\n[파이썬]")
chk(f"버전 {sys.version_info.major}.{sys.version_info.minor}",
    sys.version_info >= (3, 10), "Python 3.10 이상이 필요합니다")

print("\\n[패키지]")
for m in ("openpyxl", "xlrd", "fitz", "yaml", "PIL", "pandas",
          "streamlit", "anthropic", "dotenv", "pytest"):
    try:
        importlib.import_module(m)
        chk(m, True)
    except ImportError:
        chk(m, False, "setup.bat 을 다시 실행하세요")

print("\\n[프로젝트 파일]")
for p in ("src/contracts.py", "schema/fields.yaml", "schema/rules.yaml", "CLAUDE.md"):
    chk(p, os.path.exists(os.path.join(ROOT, p)), "Fetch/Pull 로 최신 코드를 받으세요")

print("\\n[스키마]")
try:
    import yaml
    with open(os.path.join(ROOT, "schema/fields.yaml"), encoding="utf-8") as f:
        d = yaml.safe_load(f)
    n = len(d.get("fields", []))
    chk(f"필드 {n}개 로드", n == 30, "gen_schema.py 로 재생성이 필요할 수 있습니다")
except Exception as e:
    chk("fields.yaml 파싱", False, str(e))

print("\\n[API 키]")
env = os.path.join(ROOT, ".env")
if os.path.exists(env):
    txt = open(env, encoding="utf-8", errors="ignore").read()
    chk(".env 에 키 입력됨", "여기에" not in txt and "ANTHROPIC_API_KEY=" in txt and
        len(txt.split("ANTHROPIC_API_KEY=")[-1].strip()) > 10,
        ".env 를 메모장으로 열어 API 키를 넣어주세요")
else:
    chk(".env 존재", False, "setup.bat 을 실행하세요")

print("\\n[문서 원본]")
rf = os.path.join(ROOT, "raw_file")
chk("raw_file 폴더", os.path.isdir(rf),
    "문서 원본을 raw_file 폴더에 넣어주세요 (Git 에는 올리지 않습니다)")

print("\\n" + "=" * 52)
print("  준비 완료" if ok else "  위의 [실패] 항목을 해결한 뒤 다시 실행하세요")
print("=" * 52)
''')

W(".gitignore", """
# ══════════════════════════════════════════════
#  절대 올리지 않는 것
#  주의: .gitignore 는 줄 끝 주석을 지원하지 않는다.
#        패턴과 같은 줄에 주석을 쓰면 패턴이 무효가 된다.
# ══════════════════════════════════════════════

# 회사 문서 원본 (435MB) — 반출 이슈 + 용량
raw_file/

# API 키
.env

# 강의자료·참고자료 (203MB)
backup_data/

# 실행 산출물
runs/
~$*

# 파이썬
.venv/
__pycache__/
*.pyc
.pytest_cache/
""")

W("README.md", '''
# D2S Ingestion Engine — PoC

**VLM 기반 연속공정 디지털트윈 Asset master agent 1단계 개발 PoC**

비정형 설비 문서에서 컨트롤밸브 기준정보 30필드를 추출·검증해 마스터 스키마 엑셀로
내보낸다. 사람이 최종 확정한다.

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
docs/roles/<내 이름>.md 와 CLAUDE.md 를 읽고 내 담당 모듈을 파악해줘.
fixtures 로 검증 가능한 최소 구현부터 시작하자.
```

## 구조

```
schema/       fields.yaml (30필드 정의) · rules.yaml (변환 규칙)   ← 서경빈 선임
src/
  contracts.py     계약 3개 — 이종수 책임만 수정
  triage/ router/  ① ②                                          ← 강민호 책임
  parsers/vlm/     ③-b                                           ← 강민호 책임
  parsers/text/    ③-a                                           ← 서경빈 선임
  normalize/       ④                                             ← 이종수 책임
  validate/format/ ⑤-a                                           ← 서경빈 선임
  validate/domain/ ⑤-b                                           ← 이종수 책임
  state/ ui/       ⑥ ⑦                                           ← 이종수 책임
eval/         평가 하네스 (홀드아웃 잠금 · --no-vlm)
fixtures/     모듈별 입력·기대출력 샘플
docs/         ARCHITECTURE · PRINCIPLES · GIT_GUIDE · roles
runs/         실행 로그 (Git 제외)
raw_file/     문서 원본 (Git 제외)
```

## 규칙 요약

- **자기 모듈 폴더만 수정한다**
- 규칙은 코드가 아니라 `schema/*.yaml` 에
- 모르면 값을 만들지 않는다 (`state=NA` + `note`)
- `raw_file/` 과 `.env` 는 Git 에 올리지 않는다

자세한 내용은 `CLAUDE.md` 와 `docs/PRINCIPLES.md`.

## 일정

| 날짜 | 내용 |
|---|---|
| 8/21 (금) | 스키마 · 계약 · 라벨링 착수 |
| 8/22~24 | 라벨링 완료 + MVP 수직 슬라이스 |
| 8/24~25 | 모듈 병렬 확장 |
| 8/26 (수) | 통합 → 홀드아웃 평가 |
| **8/27 (목)** | **발표** |
''')

print("생성:", len(made), "개")
for m in made:
    print("  +", m)
if skipped:
    print("건너뜀(이미 존재):", len(skipped), "개 —", ", ".join(skipped[:6]))
