# -*- coding: utf-8 -*-
"""모델 단계 — 싼 모델로 먼저, 못 읽은 것만 상위 모델로 (공용)

    from src import models

    models.for_attempt(0)   → ModelTier(name="gpt-5.6-luna",  ...)
    models.for_attempt(1)   → ModelTier(name="gpt-5.6-terra", ...)

왜 단계를 두는가 (2026-08-24 결정)
─────────────────────────────────────────────────────────────
이 과제에는 이미 비용 사다리가 있다.

    규칙 검증(무료) ─▶ 평가 Agent(유료) ─▶ HITL(가장 비싸다)

여기에 **모델 축**을 하나 더 붙인다. 1차는 싼 모델로 전량 돌리고, 못 읽은
필드만 상위 모델로 올린다.

이게 성립하는 이유는 `pipeline.py` 의 Loop A 가 **필드 단위**로 재시도하기
때문이다(`reread(path, f, prev, attempt)`). 페이지 전체를 다시 돌리지 않으므로
상위 모델은 실패한 몇 개 필드만 본다. 28필드 중 3개가 실패하면 상위 모델
비용은 그 3개분이다.

⚠️ 재시도는 **추출 실패(못 읽음)** 에만 걸린다. 제약 위반에는 걸지 않는다 —
   틀린 값을 다시 물으면 환각을 유도한다(CLAUDE.md).

컨텍스트 비용에 대하여
─────────────────────────────────────────────────────────────
"매 루프마다 Rule 과 Note 를 읽으면 컨텍스트 비용이 크지 않나" 라는 우려가
있었다. 실제 구조를 확인한 결과 **Rule 과 Note 는 모델에 가지 않는다.**

    schema/guidance.yaml   → src/ui/hitl.py 만 읽는다 (사람이 보는 화면)
    domain_rules           → pipeline.py Normalize (파이썬, 추출 이후)
    value_aliases          → pipeline.py Normalize (파이썬)
    unit_tokens            → parsers/text/units.py (파이썬)
    composite_labels       → parsers/text/composite.py (파이썬)

모델에 가는 것은 **필드 정의(name·desc·aliases)** 뿐이고, 그것도
`extract()` 로 문서당 1회다. 재시도는 실패한 필드 하나만 넘긴다.

그래서 "1차는 규칙 없이" 는 오히려 비싸진다 — aliases 가 없으면 VLM 이
`Valve Coefficient` 를 `rated_cv` 로 매핑할 수 없어 벤더 변종 대부분이 실패하고,
거의 모든 필드가 상위 모델로 올라간다.

반복되는 정적 프롬프트(필드 정의)는 **prompt caching** 으로 해결한다 —
문서마다 똑같으므로 한 번 쓰고 이후는 싸게 읽는다. 빼는 것이 아니라 캐시한다.

정말 줄이고 싶으면 줄일 자리는 두 곳이다.
    ① 페이지 수 — 8페이지를 격자 1장으로 판정한다 (이미 구현)
    ② 필드 수 — 1차는 MVP 9필드만 (`Pipeline(only_mvp=True)`, 이미 있다)
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# 기본 모델. `.env` 의 D2S_MODEL_TIER1 · D2S_MODEL_TIER2 로 덮어쓸 수 있다.
TIER1 = "gpt-5.6-luna"      # 1차 — 전량
TIER2 = "gpt-5.6-terra"     # 재시도 — 못 읽은 필드만


@dataclass(frozen=True)
class ModelTier:
    name: str
    tier: int                     # 1 = 기본, 2 = 상위
    why: str                      # 로그·화면에 남기는 선택 사유

    @property
    def is_escalated(self) -> bool:
        return self.tier > 1


def tier1() -> ModelTier:
    return ModelTier(os.getenv("D2S_MODEL_TIER1", TIER1), 1, "1차 판독")


def tier2() -> ModelTier:
    return ModelTier(os.getenv("D2S_MODEL_TIER2", TIER2), 2,
                     "1차에서 못 읽어 상위 모델로 승격")


def for_attempt(attempt: int) -> ModelTier:
    """Loop A 의 시도 번호로 모델을 고른다.

        attempt 0  1차 판독      → tier1
        attempt 1+ 재시도        → tier2

    같은 입력 → 같은 출력을 지키기 위해 시도 번호만 본다. 확신도나 난이도로
    모델을 바꾸면 재현되지 않는다(철학 6).
    """
    return tier1() if attempt <= 0 else tier2()


def summary() -> dict[str, str]:
    """실행 로그에 남길 모델 구성. 재현성 기록의 일부다."""
    return {"tier1": tier1().name, "tier2": tier2().name}
