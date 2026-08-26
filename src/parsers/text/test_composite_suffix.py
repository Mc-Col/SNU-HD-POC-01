# -*- coding: utf-8 -*-
"""값 칸에 붙은 부가 표기를 떼는 복합 규칙 검증 (2026-08-26).

무엇을 재는가
  `SUS316 (1/2")` · `4" X 2.62"` · `195 (Cg=4040 → 7580)` 처럼 우리 필드가
  아닌 값이 같은 칸에 붙어 있는 경우다. 규칙은 `schema/rules.yaml` 의
  composite_labels 에 있고 코드에는 없다(철학 2).

무엇을 지키는가
  ① 형태가 맞지 않으면 **규칙이 아예 걸리지 않는다** — 다른 문서에 영향이 없다
  ② 떼어낸 조각은 **버리지 않고 비고에 남는다** — 버린 데이터는 못 되살린다
  ③ 치수의 구두점을 살린다 — `1/2"` 를 `12"` 로 만들면 24배 틀린다
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.parsers.text.composite import (CompositeIndex, Piece,     # noqa: E402
                                        split_note, try_split)
from src.parsers.text.field_index import FieldIndex                # noqa: E402
from src.parsers.text.sections import SectionIndex                 # noqa: E402


@pytest.fixture(scope="module")
def ixs():
    six = SectionIndex.load()
    return FieldIndex.load(section_names=six.name_map()), CompositeIndex.load()


@pytest.mark.parametrize("label,value,key,kept,dropped", [
    ("Stem", 'SUS316            (1/2")', "valve_stem_material", "SUS316", '1/2"'),
    ("Body Size", '4" X 2.62"', "valve_body_size", '4"', '2.62"'),
    ("Rated Cv", "195 (Cg=4040 → 7580)", "rated_cv", "195", "Cg=4040 → 7580"),
])
def test_부가표기를_떼고_조각은_남긴다(ixs, label, value, key, kept, dropped):
    ix, cix = ixs
    got = try_split(cix, ix, label, value)
    assert got is not None, f"{label} 규칙이 걸리지 않았다"
    ok, pending = got
    assert [(p.field_key, p.value) for p in ok] == [(key, kept)]
    assert dropped in " ".join(p.value for p in pending)


@pytest.mark.parametrize("label,value", [
    ("Stem", "SUS316"),                 # 괄호가 없다
    ("Body Size", '4"'),                # X 표기가 없다
    ("Body Size", '1/2"'),              # 분수 — 손대면 12" 가 된다
    ("Body Size", "DN100"),             # 다른 표기 체계
    ("Rated Cv", "195"),                # 괄호가 없다
    ("Rated Cv", "195 (max)"),          # Cg 가 아니다 — T7 은 max 를 막지 않는다
])
def test_형태가_다르면_규칙이_걸리지_않는다(ixs, label, value):
    ix, cix = ixs
    assert try_split(cix, ix, label, value) is None


def test_Cg_만_있는_칸은_손대지_않는다(ixs):
    """Cg 는 Cv 와 다른 양이다. Cv 가 없으면 환산이 추정이 되므로 값을 만들지 않는다.

    T7 정책 — 이 경우는 값 칸을 막아야 하고 그것은 ④ Normalize·도메인 검증 몫이다.
    파서가 임의로 Cg 를 Cv 로 옮기면 20배 틀린 값이 마스터에 들어간다.
    """
    ix, cix = ixs
    assert try_split(cix, ix, "Rated Cv", "Cg=4040") is None
    assert try_split(cix, ix, "Rated Cv", "4040 (Cg)") is None


def test_비고에_미사용_조각이_남는다():
    note = split_note("Rated Cv", [Piece("note_cg", "Rated Cv → note_cg",
                                         "Cg=4040 → 7580")])
    assert "미사용 조각" in note and "Cg=4040" in note


def test_남길_조각이_없으면_문구가_늘지_않는다():
    assert split_note("Process Fluid", []) == "복합 라벨 'Process Fluid' 분해"
