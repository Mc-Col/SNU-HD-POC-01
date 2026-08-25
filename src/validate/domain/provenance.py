# -*- coding: utf-8 -*-
"""⑤-b 출처 교차검증 — 문서 안에 더 나은 출처가 있는데 안 썼는가

    from src.validate.domain import provenance
    kind, why = provenance.check(field, value, ex, context=extractions)

무엇을 잡나
─────────────────────────────────────────────────────────────
2026-08-25 실측. 같은 1986년 Fisher 서식 두 건에서 이렇게 나왔다.

    manufacturer             'FISHER'        (좌측 상단 로고)      확신도 1.00
    positioner_manufacturer  'N/MASONEILAN'  MANUFACTURER:        확신도 0.96

**문서 안에 `MANUFACTURER:` 칸이 있는데 본체 제조사는 로고에서 읽었다.**
우리 판독 규칙의 판단 순서(① Maker 항목 → ② 모델명 → ③ Note → ④ null,
로고·꼬리말 금지)를 정면으로 어긴 것이다.

확신도로는 못 잡는다(1.00 이다). 어휘로도 못 잡는다(FISHER 는 유효한 값이다).
**모델이 스스로 적은 출처끼리 모순된다** — 그것이 신호다.

왜 "로고에서 읽었다" 만으로는 표시하지 않나
─────────────────────────────────────────────────────────────
처음에는 약한 출처(로고·머리글)를 전부 표시했다. **실측 결과 23건 중 14건이
걸렸고 그중 실제 오류는 1건이었다 — 정밀도 7%.** 1986년 Fisher 서식은
발행처 로고가 곧 제조사인 경우가 대부분이라 정상이었기 때문이다.

그래서 조건을 좁혔다 — **같은 문서 안에 더 나은 출처가 실제로 존재할 때만**
표시한다. 같은 표본에서 14건 → 2건이 되고, 둘 다 진짜 오류다.

값은 바꾸지 않는다. 어느 쪽이 제조사인지는 문서를 본 사람이 정한다.
"""
from __future__ import annotations

import re

from src.contracts import FailureKind

# 모델이 "여기서 읽었다" 고 적은 말이 약한 출처를 가리키는가.
# 한국어·영어가 섞인다 — 모델이 자유롭게 적는 칸이기 때문이다.
_WEAK = re.compile(
    r"로고|logo|꼬리말|footer|머리글|header|letterhead|발행처|"
    r"양식|서식|표지|title\s*block", re.I)

# 문서가 "이것이 제조사다" 라고 명시한 자리.
_STRONG = re.compile(r"manufacturer|maker|제조사|메이커", re.I)


def _better_source(context, exclude_key: str):
    """같은 문서에서 **명시적 제조사 칸**을 읽은 항목을 찾는다.

    → (그 항목의 값, 그 라벨) 또는 None.
    """
    for key, ex in (context or {}).items():
        if key == exclude_key or ex is None:
            continue
        label = str(getattr(ex, "raw_label", "") or "")
        value = getattr(ex, "raw_value", None)
        if value and _STRONG.search(label) and not _WEAK.search(label):
            return str(value), label
    return None


def check(f, value, ex=None, context=None) -> tuple[FailureKind, str]:
    """더 나은 출처를 두고 약한 출처에서 읽었는가. → (실패유형, 사유).

    `context` 는 같은 문서의 `{field_key: RawExtraction}` 이다. 없으면
    교차검증을 할 수 없으므로 판정하지 않는다 — 조용히 통과시키는 것이
    아니라, **판단 근거가 없으면 판단하지 않는다**는 원칙이다.
    """
    if ex is None or not value or not context:
        return FailureKind.NONE, ""
    if "manufacturer" not in f.key:
        return FailureKind.NONE, ""

    label = str(getattr(ex, "raw_label", "") or "")
    if not label or not _WEAK.search(label):
        return FailureKind.NONE, ""

    other = _better_source(context, f.key)
    if other is None:
        return FailureKind.NONE, ""

    alt, alt_label = other
    return (FailureKind.CONSTRAINT,
            f"'{label}' 에서 읽었는데, 같은 문서의 '{alt_label}' 칸에는 "
            f"{alt!r} 이 적혀 있다. 판독 규칙은 표 안의 Maker 항목을 "
            f"로고·꼬리말보다 우선하라고 한다. 값은 바꾸지 않았다 — "
            f"어느 쪽이 제조사인지는 사람이 정한다")
