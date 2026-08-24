# -*- coding: utf-8 -*-
"""① Triage 상수.

여기에 **없는 것**이 중요하다 — 태그 정규식 · 문서종류 키워드 · 텍스트 레이어
임계값은 두지 않는다. 전부 `src/preprocess.py` 에 있고 그것을 조립해 쓴다.
같은 값을 두 곳에 두면 공용 결정이 바뀔 때 한쪽만 낡는다.

yaml 이관 후보 (소유자: 이종수 책임 / 제안 위치 `schema/triage.yaml`)
    아래 확신도와 임계값은 운영 중 조정되는 정책값이라 도메인 검토 대상이다(철학 2).
"""
from __future__ import annotations                      # 타입 표기 일관성 유지

from src.preprocess import TEXT_LAYER_MIN               # 공용 텍스트 임계값 (100자)

# ══════════════════════════════════════════════════════════════════
#  사양표 판정
# ══════════════════════════════════════════════════════════════════

# 텍스트로 사양표 여부를 판정하기 위한 최소 글자 수.
#   스캔본에도 도장·표제만 텍스트로 얹힌 경우가 있어, 적은 글자로 판정하면
#   사양표를 놓치거나 아닌 것을 사양표로 만든다. 그 구간은 VLM 에 넘긴다.
#
#   **자체 숫자를 두지 않고 공용 상수를 그대로 쓴다.** 지시서가 임계값
#   하드코딩을 금지하고, "임계는 100자이고 20~500자 사이에서 판정이 거의
#   변하지 않으므로 튜닝 불필요" 라고 명시한다(강민호 책임.md 69행).
MIN_TEXT_FOR_SPEC_JUDGEMENT: int = TEXT_LAYER_MIN

# 사양표 머리글 표기. 실물에서 확인된 것만 넣는다 — 짜맞추지 않는다.
#   근거: 070055 는 "Control Valve Specification", 10FV011 p1 은
#   "CONTROL VALVE SPECIFICATIONS", 10PV018 시트3 은 동일 표기다.
SPEC_HEADER_KEYWORDS: tuple[str, ...] = (
    "CONTROL VALVE SPECIFICATION",      # 단수·복수 모두 이 접두로 걸린다
    "VALVE SPECIFICATION",
    "VALVE DATA SHEET",
    "CONTROL VALVE DATA",
    "SPECIFICATION DATA SHEET",
    "CONTROL VALVE ACTION TEST",        # 시험 보고서의 사양 블록
)


# ══════════════════════════════════════════════════════════════════
#  확신도 — `TriageResult.confidence` 에 넣는 표시값
# ══════════════════════════════════════════════════════════════════
#
# ⚠️ **계산값이 아니다.** 아래 숫자는 손으로 정한 상수이고 산출식이 없다.
#    의미가 있는 것은 절대값이 아니라 **순서**뿐이다 —
#        미지원(확실) > 범위밖 > 최신성 선택 > 파일명만 > 후보 동점 > 못 찾음
#
# ⚠️ **현재 하류가 읽지 않는다.** ⑥ State 가 임계값과 비교하는 것은
#    `FieldRecord.confidence`(파서가 필드마다 낸 값)와 schema 의 필드별
#    `threshold` 이지 이 값이 아니다. 이름이 같아 혼동하기 쉽다.
#    지금은 로그·화면에 남는 표시값이다.
#
# 캘리브레이션(예: "이 판정이 맞을 사후확률")은 골든셋 표본이 10건뿐이라
# 지금은 불가능하다. 하류가 문서 단위 확신도를 쓰기로 정해지면 그때
# 정의하고 `schema/triage.yaml` 로 옮긴다.

# 사양표를 최신성 규칙으로 골랐다 — 근거가 가장 강하다.
CONFIDENCE_SPEC_SELECTED: float = 0.90

# 텍스트 근거 없이 파일명만으로 통과시켰다 (스캔 문서).
#   페이지를 고르지 않은 상태이므로 낮게 준다 — 사람이 볼 여지를 남긴다.
CONFIDENCE_FILENAME_ONLY: float = 0.45

# 파일명으로 제외했다 — 판정 자체는 확실하다.
CONFIDENCE_OUT_OF_SCOPE: float = 0.95

# 미지원 포맷 — 확장자만 보면 되므로 확실하다.
CONFIDENCE_UNSUPPORTED: float = 1.00

# 사양표 후보가 2장 이상인데 최신성으로 가리지 못했다.
#   지시서 118행 — "못 가릴 때 아무거나 고르지 말 것. None + 사유가 정답이다."
#   그래서 `targets` 를 비우고 이 확신도로 사람에게 넘긴다. 값을 만들지 않는다.
CONFIDENCE_AMBIGUOUS_SPEC: float = 0.30

# 사양표를 못 찾고 렌더도 실패했다 — 사람이 확인해야 한다.
CONFIDENCE_NO_SPEC: float = 0.20


# ══════════════════════════════════════════════════════════════════
#  렌더
# ══════════════════════════════════════════════════════════════════

# 렌더 위치를 지정받지 못했을 때 쓸 임시 폴더 이름.
#   파이프라인이 실행 폴더(runs/<id>/)를 넘기면 그쪽을 쓴다.
RENDER_SUBDIR: str = "d2s_triage_render"


YAML_MIGRATION_CANDIDATES: tuple[str, ...] = (
    # MIN_TEXT_FOR_SPEC_JUDGEMENT 는 제외한다 — 공용 preprocess.TEXT_LAYER_MIN
    # 을 참조하므로 이관 대상은 이 모듈이 아니라 공용 쪽이다.
    "SPEC_HEADER_KEYWORDS",          # 도메인 표기 — 실물에서 계속 수집된다
    "CONFIDENCE_SPEC_SELECTED",      # 정책값
    "CONFIDENCE_FILENAME_ONLY",      # 동일
    "CONFIDENCE_AMBIGUOUS_SPEC",     # 동일
    "CONFIDENCE_OUT_OF_SCOPE",       # 동일
    "CONFIDENCE_NO_SPEC",            # 동일
)
