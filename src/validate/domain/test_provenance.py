# -*- coding: utf-8 -*-
"""출처 교차검증 자체 검증.

실측 사례를 그대로 넣는다 — 같은 문서에서 본체 제조사는 로고에서,
포지셔너 제조사는 `MANUFACTURER:` 칸에서 읽었다. 확신도는 1.00 이었다.

**절대 표시해선 안 되는 것**을 나란히 둔다 — 1986년 Fisher 서식은 로고가
곧 제조사인 경우가 대부분이고, 그걸 전부 표시하면 정밀도가 7% 로 떨어진다
(실측). 그래서 "더 나은 출처가 실제로 있을 때만" 이 조건의 핵심이다.
"""
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import schema                                        # noqa: E402
from src.contracts import FailureKind, ParserType, RawExtraction   # noqa: E402
from src.validate.domain import provenance as P               # noqa: E402


def _ex(key, value, label, conf=1.0):
    return RawExtraction(field_key=key, raw_value=value, raw_label=label,
                         confidence=conf, parser=ParserType.VLM)


# 실측 그대로 — 19XV036 · 15FV037
CONFLICT = {
    "manufacturer": _ex("manufacturer", "FISHER", "(좌측 상단 로고)"),
    "positioner_manufacturer": _ex(
        "positioner_manufacturer", "N/MASONEILAN", "MANUFACTURER:", 0.96),
}


def test_문서에_제조사_칸이_있는데_로고를_읽으면_확인필요():
    kind, why = P.check(schema.get("manufacturer"), "FISHER",
                        CONFLICT["manufacturer"], CONFLICT)
    assert kind is FailureKind.CONSTRAINT
    assert "N/MASONEILAN" in why and "값은 바꾸지 않았다" in why


def test_확신도가_1이어도_잡힌다():
    """이 검사의 존재 이유 — 확신도 게이트도 어휘도 못 잡는다."""
    assert CONFLICT["manufacturer"].confidence == 1.0
    assert P.check(schema.get("manufacturer"), "FISHER",
                   CONFLICT["manufacturer"], CONFLICT)[0] is FailureKind.CONSTRAINT


# ── 표시해선 안 되는 것 ─────────────────────────────────────────

@pytest.mark.parametrize("label", ["(좌측 상단 로고)", "(머리글)", "(로고)"])
def test_더_나은_출처가_없으면_로고여도_통과(label):
    """1986년 Fisher 서식이 이 경우다 — 로고가 곧 제조사다.

    전부 표시했더니 실측 정밀도가 7% 였다(14건 표시 · 실제 오류 1건).
    """
    ctx = {"manufacturer": _ex("manufacturer", "FISHER", label),
           "model_no": _ex("model_no", "657-ED", "Size and Type")}
    assert P.check(schema.get("manufacturer"), "FISHER",
                   ctx["manufacturer"], ctx)[0] is FailureKind.NONE


def test_출처가_이미_Maker_항목이면_통과():
    ctx = {"manufacturer": _ex("manufacturer", "FISHER", "Maker (Maker 항목)")}
    assert P.check(schema.get("manufacturer"), "FISHER",
                   ctx["manufacturer"], ctx)[0] is FailureKind.NONE


def test_모델명_판단은_약한_출처가_아니다():
    """판독 규칙이 ②번으로 허용한 경로다."""
    ctx = {"manufacturer": _ex("manufacturer", "FISHER", "(모델명으로 판단)"),
           "positioner_manufacturer": _ex(
               "positioner_manufacturer", "N/MASONEILAN", "MANUFACTURER:")}
    assert P.check(schema.get("manufacturer"), "FISHER",
                   ctx["manufacturer"], ctx)[0] is FailureKind.NONE


@pytest.mark.parametrize("key", [
    "model_no", "rated_cv", "valve_body_material", "actuator_fail_action",
])
def test_제조사_계열이_아니면_판정하지_않는다(key):
    ctx = {key: _ex(key, "X", "로고"),
           "manufacturer": _ex("manufacturer", "Y", "MANUFACTURER:")}
    assert P.check(schema.get(key), "X", ctx[key], ctx)[0] is FailureKind.NONE


def test_문맥이_없으면_판정하지_않는다():
    """판단 근거가 없으면 판단하지 않는다 — 조용히 통과가 아니라 원칙이다."""
    assert P.check(schema.get("manufacturer"), "FISHER",
                   CONFLICT["manufacturer"], None)[0] is FailureKind.NONE


@pytest.mark.parametrize("bad", [None, ""])
def test_값이나_추출이_없으면_판정하지_않는다(bad):
    assert P.check(schema.get("manufacturer"), bad,
                   CONFLICT["manufacturer"], CONFLICT)[0] is FailureKind.NONE
    assert P.check(schema.get("manufacturer"), "FISHER",
                   None, CONFLICT)[0] is FailureKind.NONE


def test_값을_바꾸지_않는다():
    ex = _ex("manufacturer", "FISHER", "(좌측 상단 로고)")
    P.check(schema.get("manufacturer"), "FISHER", ex,
            {**CONFLICT, "manufacturer": ex})
    assert ex.raw_value == "FISHER"
