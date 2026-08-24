# -*- coding: utf-8 -*-
"""기하 전처리·캐시 상수.

호출 파라미터(모델·토큰·프롬프트)는 여기 없다 — `openai_vlm.py` 와 `src/models.py`
가 갖는다. 이 파일은 **보조 자산 두 개가 쓰는 값만** 남긴다.

yaml 이관 후보 (소유자: 이종수 책임)
    이 값들은 코드가 아니라 `schema/*.yaml` 에 있어야 한다(철학 2). 스캐너 특성과
    비용·품질 트레이드오프라 도메인 검토 대상이고, 코퍼스가 바뀌면 재측정해야 한다.
    제안 위치는 `schema/vlm.yaml` 이며, 목록은 아래 YAML_MIGRATION_CANDIDATES 에 있다.
"""
from __future__ import annotations                      # 타입 표기 일관성 유지

import os                                               # 캐시 기본 경로 조립
from pathlib import Path                                # 저장소 루트 계산

# 저장소 루트 — 이 파일이 src/parsers/vlm/ 에 있으므로 3단계 위가 루트다.
REPO_ROOT: Path = Path(__file__).resolve().parents[3]


# ══════════════════════════════════════════════════════════════════
#  ① 기하 전처리 — 방향 보정
# ══════════════════════════════════════════════════════════════════

# 평가할 회전 후보. 180·270 은 넣지 않는다 — 행 방향 투영 점수가 뒤집힘을
# 구분하지 못하기 때문이다(0°와 180°의 프로파일이 사실상 같다).
ORIENTATION_CANDIDATES: tuple[int, ...] = (0, 90)

# 90도 후보를 채택하기 위한 여유 배율. 애매할 때 회전하지 않는 쪽으로 기울인다 —
# 정상 페이지를 90도 돌리는 오작동이 훨씬 치명적이다.
# 실측: 세로 스캔 190건에 대해 오탐 0건.
ORIENTATION_MARGIN_RATIO: float = 1.15


# ══════════════════════════════════════════════════════════════════
#  ② 기하 전처리 — 기울기 보정
# ══════════════════════════════════════════════════════════════════

# 탐색 범위(±도). 실측 최대 기울기가 3.70도이므로 5도면 충분하다.
DESKEW_SEARCH_RANGE_DEGREES: float = 5.0

# 1단계 개략 탐색 간격.
DESKEW_COARSE_STEP_DEGREES: float = 1.0

# 2단계 정밀 탐색 간격. 적용 임계값이 0.5도이므로 이보다 고운 정밀도는 필요 없다.
#   실측: 후보 32개 중 21개가 정밀 탐색이라 건당 1,331ms 의 대부분을 차지한다.
#   0.25 로 올리면 후보가 20개로 줄어 약 32% 빨라진다 (정확도 손실 없음).
DESKEW_FINE_STEP_DEGREES: float = 0.25

# 정밀 탐색 창(개략 최적점 ±도).
DESKEW_FINE_WINDOW_DEGREES: float = 1.0

# 실제로 회전을 적용할 최소 각도. 이하는 재샘플링 손실이 이득보다 크다.
#   실측: 스캔 758건 중 71.5%(542건)가 이 값을 초과해 보정이 발동한다.
DESKEW_MIN_APPLY_DEGREES: float = 0.5

# 각도 탐색용 축소 크기(장변 px). 각도 추정은 저해상도로 충분하고 훨씬 빠르다.
DESKEW_PROBE_LONG_EDGE: int = 1000

# 점수 계산에 쓸 중앙 창 비율. 회전마다 달라지는 모서리 결손을 점수에서 배제한다.
DESKEW_SCORE_WINDOW_RATIO: float = 0.8


# ══════════════════════════════════════════════════════════════════
#  ③ 기하 전처리 — 해상도·배경
# ══════════════════════════════════════════════════════════════════

# 목표 장변(px). 모델의 고해상도 비전 상한이며, 초과하면 서버가 임의로 축소해
# 재현성이 깨진다. 실측 코퍼스는 전부 1753px 이라 이 단계는 발동하지 않는다.
TARGET_LONG_EDGE_PX: int = 2576

# 확대를 시작하는 기준(목표의 이 비율 이하일 때만). 없는 화질을 만들지 못한다.
UPSCALE_TRIGGER_RATIO: float = 0.5

# 확대 배율 상한.
MAX_UPSCALE_FACTOR: float = 2.0

# 회전으로 생기는 빈 영역의 배경색. 스캔 문서의 지면과 같은 흰색.
#   컬러(RGB) 이미지용이며, 흑백(L·1) 은 PIL 이 3원소 튜플을 거부하므로
#   preprocess 가 모드에 맞는 스칼라를 따로 쓴다.
BACKGROUND_RGB: tuple[int, int, int] = (255, 255, 255)


# ══════════════════════════════════════════════════════════════════
#  ④ 응답 캐시
# ══════════════════════════════════════════════════════════════════

# 기본 캐시 위치. runs/ 는 .gitignore 대상이라 산출물이 저장소에 들어가지 않는다.
DEFAULT_CACHE_DIR: str = os.path.join(str(REPO_ROOT), "runs", "vlm_cache")

# 프롬프트 버전. 캐시 키의 구성요소이므로 프롬프트를 고치면 반드시 올린다.
#   올리지 않으면 바뀐 프롬프트가 옛 응답을 재사용해 개선을 측정할 수 없게 된다.
PROMPT_VERSION: str = "openai-vlm-v1"


# ══════════════════════════════════════════════════════════════════
#  yaml 이관 후보 목록
# ══════════════════════════════════════════════════════════════════

YAML_MIGRATION_CANDIDATES: tuple[str, ...] = (
    "TARGET_LONG_EDGE_PX",              # 모델 교체 시 바뀜
    "UPSCALE_TRIGGER_RATIO",            # 동일
    "MAX_UPSCALE_FACTOR",               # 동일
    "DESKEW_SEARCH_RANGE_DEGREES",      # 스캐너 특성 — 코퍼스가 바뀌면 재측정
    "DESKEW_MIN_APPLY_DEGREES",         # 동일
    "DESKEW_COARSE_STEP_DEGREES",       # 비용↔정확도 트레이드오프
    "DESKEW_FINE_STEP_DEGREES",         # 동일
    "DESKEW_PROBE_LONG_EDGE",           # 동일
    "DESKEW_SCORE_WINDOW_RATIO",        # 동일
    "ORIENTATION_MARGIN_RATIO",         # 오작동 허용치 정책값
    "PROMPT_VERSION",                   # 프롬프트 변경 이력과 함께 관리되어야 함
)
