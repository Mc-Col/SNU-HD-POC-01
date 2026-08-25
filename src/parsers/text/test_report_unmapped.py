# -*- coding: utf-8 -*-
"""미매핑 라벨 리포트 도구 검증. fixtures 로만 돌린다."""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.parsers.text.composite import CompositeIndex          # noqa: E402
from src.parsers.text.field_index import FieldIndex, normalize_label   # noqa: E402
from src.parsers.text.report_unmapped import (                 # noqa: E402
    collect, render, suggest,
)

FIX = os.path.join(ROOT, "fixtures", "text")


@pytest.fixture(scope="module")
def ix():
    return FieldIndex.load()


@pytest.fixture(scope="module")
def names(ix):
    import yaml
    from src.parsers.text.field_index import SCHEMA_PATH
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        return [(normalize_label(x["name"]), x["name"])
                for x in yaml.safe_load(f)["fields"]]


@pytest.mark.parametrize("label,expected", [
    ("Body Material", "VALVE BODY MATERIAL"),
    ("Body Size", "VALVE BODY SIZE"),
    ("Cage Material", "VALVE CAGE MATERIAL"),
    ("Model", "MODEL NO."),
])
def test_비슷한_표기를_표준_필드로_추천한다(label, expected, ix, names):
    assert suggest(label, ix, names)[0] == expected


@pytest.mark.parametrize("junk", ["金", "Y", "mm", "CE", "(dBA)", "6.00 in"])
def test_짧은_조각을_억지로_추천하지_않는다(junk, ix, names):
    """부분일치 가산이 짧은 문자열에 걸리면 오탐이 쏟아진다."""
    assert suggest(junk, ix, names)[0] == ""


def test_미매핑_라벨을_모으고_추천을_붙인다(ix):
    cix = CompositeIndex.load()
    rep = collect([os.path.join(FIX, "excel_basic.xlsx"),
                   os.path.join(FIX, "pdf_basic.pdf")], ix, cix, keep_values=False)
    assert len(rep.coverage) == 2
    assert not rep.skipped
    texts = {s.text for s in rep.labels.values()}
    assert "Spring Range" in texts          # 스키마에 없는 항목
    # 'Body Size' 는 2026-08-25 유사표현으로 등록되어 더 이상 미매핑이 아니다

    md, tsv = render(rep, ix, cix, top=50)
    assert "추천 필드가 있는 라벨" in md
    assert "Spring Range" in md             # 남은 미매핑 라벨이 리포트에 실려야 한다
    assert tsv.splitlines()[0].endswith("채택(O/X)")


def test_값은_기본적으로_싣지_않는다(ix):
    """라벨링 중인 문서의 정답을 미리 보면 앵커링된다."""
    rep = collect([os.path.join(FIX, "excel_basic.xlsx")], ix,
                  CompositeIndex.load(), keep_values=False)
    assert all(not s.example_value for s in rep.labels.values())


def test_미지원_포맷은_사유와_함께_건너뛴다(ix):
    rep = collect([os.path.join(FIX, "build_excel_basic.py")], ix,
                  CompositeIndex.load(), keep_values=False)
    assert len(rep.skipped) == 1
    assert rep.skipped[0][1]                # 사유가 비어 있지 않다
