# -*- coding: utf-8 -*-
"""텍스트 PDF 파서 자기 검증. fixtures/text/pdf_basic.pdf 로 돌린다."""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.contracts import ParserType                    # noqa: E402
from src.parsers.text import parse_pdf_text             # noqa: E402

FIXTURE = os.path.join(ROOT, "fixtures", "text", "pdf_basic.pdf")
EXPECTED = os.path.join(ROOT, "fixtures", "text", "pdf_basic.expected.json")


@pytest.fixture(scope="module")
def expected() -> dict:
    with open(EXPECTED, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def parsed():
    return parse_pdf_text(FIXTURE)


def test_기대출력과_정확히_일치한다(parsed, expected):
    got = [
        {"field_key": r.field_key, "raw_value": r.raw_value, "raw_label": r.raw_label,
         "source_locator": r.source_locator, "page": r.page,
         "confidence": r.confidence, "note": r.note}
        for r in parsed.records
    ]
    assert got == expected["records"]


def test_미매핑_라벨을_삼키지_않는다(parsed, expected):
    got = [{"text": u.text, "source_locator": u.source_locator,
            "neighbor_value": u.neighbor_value} for u in parsed.unmapped]
    assert got == expected["unmapped"]


def test_콜론_배치를_읽는다(parsed):
    """머리글의 'Label : Value' 한 덩어리."""
    by = parsed.by_key()
    assert by["engineering_tag_no"].raw_value == "11-FV-999"
    assert by["engineering_tag_no"].raw_label == "Tag"


def test_좌우_2단_배치를_읽는다(parsed):
    """라벨 x380 · 값 x448 오른쪽 단."""
    assert parsed.by_key()["actuator_fail_action"].raw_value == "Air Fails Valve to Close"


def test_MaxNorMin_중_Normal_열을_고른다(parsed):
    """2026-08-24 확정 — 공정 조건은 Normal 값을 쓴다."""
    r = parsed.by_key()["viscosity"]
    assert r.raw_value == "0.72"            # Max 0.51 / Nor 0.72 / Min 0.93
    assert "Normal" in r.note


def test_열_머리글_행은_값으로_잡지_않는다(parsed):
    labels = {r.raw_label for r in parsed.records}
    assert not ({"Max", "Nor", "Min", "Driving Cond."} & labels)


def test_먼저_찾은_값이_이긴다(parsed):
    """2페이지의 MASONEILAN 이 1페이지 값을 덮어쓰지 않는다."""
    assert parsed.by_key()["manufacturer"].raw_value == "FISHER"


def test_근거_좌표를_남긴다(parsed):
    for r in parsed.records:
        assert r.parser is ParserType.PDF_TEXT
        assert r.bbox is not None and len(r.bbox) == 4
        assert r.source_locator.startswith("p")


def test_페이지를_지정하면_그_페이지만_본다():
    only2 = parse_pdf_text(FIXTURE, pages=[2])
    assert only2.by_key()["manufacturer"].raw_value == "MASONEILAN"


def test_같은_입력이면_같은_출력이다():
    a, b = parse_pdf_text(FIXTURE), parse_pdf_text(FIXTURE)
    assert [(r.field_key, r.raw_value, r.source_locator) for r in a.records] == \
           [(r.field_key, r.raw_value, r.source_locator) for r in b.records]
