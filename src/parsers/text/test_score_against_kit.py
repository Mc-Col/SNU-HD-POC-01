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


# ── 라벨러별 집계 (2026-08-26) ──────────────────────────────────


def test_라벨러를_킷에서_읽는다(sc):
    """골든셋에 사람 라벨과 AI 초안이 섞여 있다. 누가 만든 정답인지 알아야 나눠 셀 수 있다."""
    assert set(sc.labelers()) == {"사람", "AI초안(Claude)"}
    assert all(c.labeler for c in sc.cells)


def test_라벨러별로_따로_센다(sc):
    """합쳐서 세면 'AI 가 만든 정답으로 AI 를 채점' 한 부분이 숫자에 섞인다."""
    total = sc.counts()
    parts = [sc.counts(w) for w in sc.labelers()]
    for key in total:
        assert total[key] == sum(p[key] for p in parts)


def test_리포트에_라벨러_표와_주의가_남는다(sc):
    from src.parsers.text.score_against_kit import render
    md = render(sc)
    assert "## 라벨러별" in md
    assert "사람이 검증한 정답만" in md
    assert "AI 초안은 사람 검증 전이다" in md


# ── 규칙 축 — 규칙해소 / 규칙공백 (2026-08-26) ──────────────────
#
# 왜 축을 나눴나  정규화대기 52칸을 실측하니 46칸은 이미 규칙이 처리하고
# 6칸만 남았다. 나누지 않으면 끝난 일이 할 일처럼 보인다.


def test_규칙_축은_판정과_따로_매겨진다(sc):
    """파서 판정에 규칙을 섞으면 파서 결함이 규칙에 가려진다."""
    for c in sc.cells:
        if c.verdict in ("정규화대기", "오답"):
            assert c.rule_state in ("규칙해소", "규칙공백"), c
        else:
            assert c.rule_state == "", c        # 이미 맞았거나 아예 못 집은 칸


def test_규칙_축_합계가_판정_합계와_맞는다(sc):
    r = sc.rule_counts()
    n = sc.counts()
    assert r["규칙해소"] + r["규칙공백"] == n["정규화대기"] + n["오답"]


def test_규칙공백만_다음_작업_목록에_남는다(sc):
    assert all(c.rule_state == "규칙공백" for c in sc.gaps())
    assert len(sc.gaps()) == sc.rule_counts()["규칙공백"]


def test_라벨을_넘겨야_FAIL_어간_규칙이_걸린다():
    """값만 넘기면 방향을 못 읽는다 — RawExtraction 통째로 줘야 한다."""
    from src.contracts import RawExtraction
    from src.parsers.text.score_against_kit import _normalized

    with_label = RawExtraction(field_key="actuator_fail_action", raw_value="CLOSE",
                               raw_label="Air Fails Valve to : Close", confidence=0.9)
    without = RawExtraction(field_key="actuator_fail_action", raw_value="CLOSE",
                            raw_label=None, confidence=0.9)
    assert _normalized("actuator_fail_action", with_label) == "FAIL CLOSE"
    assert _normalized("actuator_fail_action", without) != "FAIL CLOSE"


def test_리포트에_세_숫자와_규칙공백_절이_남는다(sc):
    md = render(sc)
    assert "파서 관점 성공률" in md
    assert "완전 일치율" in md
    assert "정규화 후 일치율" in md
    if sc.gaps():
        assert "규칙공백 — 다음에 손볼 칸" in md
