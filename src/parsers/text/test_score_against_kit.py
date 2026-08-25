# -*- coding: utf-8 -*-
"""골든셋 채점 도구 검증. fixtures/text/kit_mini.xlsx 로만 돌린다."""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.parsers.text.field_index import FieldIndex            # noqa: E402
from src.parsers.text.score_against_kit import read_kit, render, score   # noqa: E402

FIX = os.path.join(ROOT, "fixtures", "text")
KIT = os.path.join(FIX, "kit_mini.xlsx")


@pytest.fixture(scope="module")
def sc():
    return score(KIT, FIX)


def test_킷_구조를_읽는다():
    rows, cols, unresolved = read_kit(KIT, FieldIndex.load())
    assert [r["doc_id"] for r in rows] == ["d001", "d002", "d003"]
    assert {k for _, k, _ in cols} == {
        "engineering_tag_no", "manufacturer", "model_no", "actuator_fail_action",
        "rated_cv"}
    # 2026-08-24 — RATED CV 가 스키마에 생겼다(RATED CV MAX/NORMAL 병합).
    # 이제 대응되지 않는 킷 이름은 없다.
    assert unresolved == []


def test_판정_결과(sc):
    n = sc.counts()
    # 유사표현 반영(2026-08-25)으로 미추출 1건이 정확으로 바뀌었다
    assert n == {"정확": 5, "표기차이": 0, "정규화대기": 2, "오답": 0, "미추출": 1}


def test_표준값_변환은_파서_책임이_아니다(sc):
    """문서 'Fail Position: CLOSE' → 파서 'CLOSE' → 표준값 'FAIL CLOSE'."""
    c = next(x for x in sc.cells if x.doc_id == "d001"
             and x.field_key == "actuator_fail_action")
    assert (c.got, c.truth, c.verdict) == ("CLOSE", "FAIL CLOSE", "정규화대기")


def test_out_of_scope_문서는_채점하지_않는다(sc):
    assert ("d003", "excel_basic.xlsx", "제외(out_of_scope)") in sc.docs
    assert not [c for c in sc.cells if c.doc_id == "d003"]


def test_정답이_NA_인_칸은_채점에서_뺀다(sc):
    assert not [c for c in sc.cells if c.truth.upper() == "N/A"]


def test_대응_필드가_없는_킷_컬럼을_드러낸다(sc):
    """킷에 있으나 스키마에 없는 이름은 조용히 버리지 않고 드러낸다.

    2026-08-24 현재 kit_mini 의 5개 컬럼이 모두 스키마에 대응되어 목록이 비었다.
    비어 있음을 단정해 두면, 나중에 스키마가 다시 바뀌어 대응이 깨질 때
    이 테스트가 먼저 알려준다.
    """
    assert sc.unscorable_fields == []


def test_같은_입력이면_같은_출력이다():
    a, b = score(KIT, FIX), score(KIT, FIX)
    assert [(c.doc_id, c.field_key, c.verdict) for c in a.cells] == \
           [(c.doc_id, c.field_key, c.verdict) for c in b.cells]
