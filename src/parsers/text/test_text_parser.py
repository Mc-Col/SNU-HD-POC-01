# -*- coding: utf-8 -*-
"""fixtures/text/ 로 자기 검증한다. 남의 모듈을 기다리지 않는다."""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.contracts import ParserType                       # noqa: E402
from src.parsers.text import FieldIndex, normalize_label, parse_excel   # noqa: E402

FIXTURE = os.path.join(ROOT, "fixtures", "text", "excel_basic.xlsx")
EXPECTED = os.path.join(ROOT, "fixtures", "text", "excel_basic.expected.json")


@pytest.fixture(scope="module")
def expected() -> dict:
    with open(EXPECTED, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def parsed():
    return parse_excel(FIXTURE)


# ── 라벨 정규화 ────────────────────────────────────────────────
@pytest.mark.parametrize("raw,norm", [
    ("Model No.", "MODELNO"),
    ("MODEL NO", "MODELNO"),
    ("  model  no.  ", "MODELNO"),
    ("Req'd Flow Coeff., Cv", "REQDFLOWCOEFFCV"),
    (None, ""),
])
def test_라벨_정규화는_표기_흔들림을_흡수한다(raw, norm):
    assert normalize_label(raw) == norm


def test_유사표현은_스키마에서만_읽는다():
    ix = FieldIndex.load()
    # 필드 수를 숫자로 박지 않는다. 표준은 실물 라벨링을 거치며 바뀐다
    # (30 → 28: C027 에서 POSITIONER TYPE·MIN/MAX TEMP 삭제, RATED CV MAX 병합).
    # 스키마가 담은 수와 같은지만 본다 — FieldIndex 가 조용히 빠뜨리지 않는지 확인.
    from src import schema as _schema
    assert ix.field_count == len(_schema.all_fields())
    assert ix.lookup("Tag").key == "engineering_tag_no"
    assert ix.lookup("Fail Position").key == "actuator_fail_action"
    assert ix.collisions == []


# ── 파서 출력 ──────────────────────────────────────────────────
def test_기대출력과_정확히_일치한다(parsed, expected):
    got = [
        {
            "field_key": r.field_key,
            "raw_value": r.raw_value,
            "raw_label": r.raw_label,
            "source_locator": r.source_locator,
            "page": r.page,
            "confidence": r.confidence,
        }
        for r in parsed.records
    ]
    assert got == expected["records"]


def test_미매핑_라벨을_삼키지_않는다(parsed, expected):
    got = [
        {"text": u.text, "source_locator": u.source_locator,
         "neighbor_value": u.neighbor_value}
        for u in parsed.unmapped
    ]
    assert got == expected["unmapped"]


def test_raw_label_은_문서_표기_그대로다(parsed):
    """유사표현 사전이 여기서 자란다 — 표준명으로 덮어쓰면 안 된다."""
    by = parsed.by_key()
    assert by["engineering_tag_no"].raw_label == "Tag"
    assert by["actuator_fail_action"].raw_label == "Fail Position"


def test_값이_없으면_만들어내지_않는다(parsed):
    """모르면 None. 추정값을 넣지 않는다."""
    r = parsed.by_key()["viscosity"]
    assert r.raw_value is None
    assert r.found is False
    assert r.note                                   # 사유를 반드시 남긴다


def test_먼저_찾은_값이_이긴다(parsed):
    """PHOTO 시트의 MASONEILAN 이 SPEC 시트 값을 덮어쓰지 않는다."""
    assert parsed.by_key()["manufacturer"].raw_value == "FISHER"


def test_계약_타입을_그대로_반환한다(parsed):
    for r in parsed.records:
        assert r.parser is ParserType.EXCEL
        assert r.source_locator and "!" in r.source_locator


def test_같은_입력이면_같은_출력이다():
    a, b = parse_excel(FIXTURE), parse_excel(FIXTURE)
    assert [r.field_key for r in a.records] == [r.field_key for r in b.records]
    assert [r.source_locator for r in a.records] == [r.source_locator for r in b.records]
