# -*- coding: utf-8 -*-
"""② Router 상수.

여기에 **없는 것**이 중요하다 — 확장자 목록과 텍스트 레이어 임계값은 두지 않는다.
확장자는 `preprocess.EXCEL_EXT` · `PDF_EXT` · `IMAGE_EXT` 를, 텍스트 레이어 유무는
`preprocess.probe_pages` 가 판정한 `has_text_layer` 를 쓴다. 같은 값을 두 곳에
두면 지원 범위가 바뀔 때 한쪽만 낡는다.

이전 구현에는 `TEXT_RATIO_THRESHOLD`(0.70)와 `MIN_CHARS_PER_TEXT_PAGE`(50)가
있었다. 텍스트냐 VLM 이냐를 가르는 값이었는데, 그 판정 자체를 폐기했으므로
상수도 없앤다 — 텍스트 레이어가 있어도 VLM 을 건너뛰지 않는다(CLAUDE.md).
"""
from __future__ import annotations                      # 타입 표기 일관성 유지

from src.preprocess import TEXT_LAYER_MIN               # 공용 텍스트 임계값 (100자)

# ══════════════════════════════════════════════════════════════════
#  리더 이름 — 판정 근거에 남겨 하류가 어떤 경로였는지 알 수 있게 한다
# ══════════════════════════════════════════════════════════════════

READER_OPENPYXL: str = "openpyxl"    # xlsx · xlsm
READER_XLRD: str = "xlrd"            # xls (BIFF) — openpyxl 로는 읽을 수 없다
READER_PYMUPDF: str = "pymupdf"      # pdf · tif 렌더
READER_NONE: str = "none"            # 처리하지 않음


# ══════════════════════════════════════════════════════════════════
#  텍스트 보조 근거
# ══════════════════════════════════════════════════════════════════

# 텍스트를 "보조 근거로 쓸 만하다" 고 볼 최소 글자 수.
#   경로를 가르는 값이 **아니다** — 경로는 포맷으로 정하고, 이 값은 하류에
#   "이 페이지는 텍스트를 대조에 쓸 수 있다" 고 알려주는 표시일 뿐이다.
#   근거: 스캔본에 도장·표제만 텍스트로 얹힌 경우가 있어(수십 자) 그것을
#   근거로 쓰면 오히려 오독을 유도한다.
#
#   **자체 숫자를 두지 않고 공용 상수를 그대로 쓴다.** CLAUDE.md 하지 말 것 —
#   "임계값을 하드코딩하지 말 것 — preprocess.TEXT_LAYER_MIN (100자).
#   20~500자 사이에서 판정이 거의 변하지 않으므로 튜닝은 불필요하다."
TEXT_HINT_MIN_CHARS: int = TEXT_LAYER_MIN


# 이관 후보 없음 — 이 모듈의 임계값은 전부 공용 preprocess 상수를 참조한다.
YAML_MIGRATION_CANDIDATES: tuple[str, ...] = ()
