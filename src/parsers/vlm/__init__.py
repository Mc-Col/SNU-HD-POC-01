# -*- coding: utf-8 -*-
"""③-b VLM PARSER — 이미지에서 값과 위치를

본체는 `openai_vlm.VlmParser` 다. 이 패키지는 그 위에 두 가지 보조 자산을 더한다.

    ① 기하 전처리 (`preprocess`, `transform`)
       스캔 758건 실측에서 **71.5% 가 기울기 0.5도를 초과**한다. 공용
       `src/preprocess.py` 는 페이지를 렌더하지만 기하 보정은 하지 않으므로,
       기울어진 페이지가 그대로 모델에 들어간다. 보정하면 글줄 투영 점수가
       중앙값 159% 개선되고 잔여 기울기가 0 으로 수렴한다.
       **좌표 역변환 체인이 함께 있어야** 회전 후에도 bbox 가 원본 좌표를 가리킨다.

    ② 응답 캐시 (`cache`)
       같은 입력에 같은 출력을 보장하는 유일한 수단이다(철학 6). VLM API 는
       완전한 결정론이 아니므로, (파일해시, 페이지, 프롬프트버전) 으로 응답을
       보관해야 파이프라인 재실행이 재현된다.

둘 다 **기본 꺼짐**이다. `VlmParser(deskew=True, cache=...)` 로 켠다 —
검증되지 않은 동작을 기본으로 만들지 않는다.
"""
from .cache import (                                    # 재현성 캐시
    MemoryResponseCache,
    ResponseCache,
    cache_key,
    hash_source,
)
from .openai_vlm import VlmParser                        # ③-b 본체 (정본)
from .preprocess import (                               # 기하 전처리
    Preprocessed,
    estimate_orientation,
    estimate_skew,
    plan_scale,
    preprocess,
)
from .transform import PageTransform                      # 좌표 역변환 체인

__all__ = [
    # ── 본체 ────────────────────────────────────────────
    "VlmParser",                # 추출·재판독 (openai_vlm)
    # ── 기하 전처리 ─────────────────────────────────────
    "preprocess",               # 방향·기울기·해상도 보정 + 변환 기록
    "Preprocessed",             # 전처리 결과 묶음 (이미지 + 변환 + 원본)
    "PageTransform",            # 원본 ↔ 전처리 좌표 대응 (역변환 포함)
    "estimate_orientation",     # 90도 방향 추정
    "estimate_skew",            # 기울기 추정
    "plan_scale",               # 해상도 배율 계산
    # ── 재현성 캐시 ─────────────────────────────────────
    "ResponseCache",            # 디스크 캐시
    "MemoryResponseCache",      # 메모리 캐시 (테스트용)
    "cache_key",                # (파일해시, 페이지, 프롬프트버전) 키
    "hash_source",              # 파일/픽셀 지문
]
