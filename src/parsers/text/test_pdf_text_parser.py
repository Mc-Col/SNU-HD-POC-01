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
    assert parsed.by_key()["valve_leakage_class"].raw_value == "ANSI IV"


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


def test_복합_라벨을_쪼갠다(parsed):
    """'Size/Pressure Class/Body Form' = '4 / 300 / Cast' → 앞 두 조각만.

    세 번째 Body Form(Cast)은 주조·단조 구분이라 대응 필드가 없다.
    밸브 형식은 'Valve Model / Body Type' = 'Mark One / Globe / Standard' 에서 온다.
    (2026-08-25 실물 19FV077 에서 확인 — 두 칸이 따로 있다)
    """
    by = parsed.by_key()
    assert (by["valve_body_rating"].raw_value, by["valve_body_rating"].raw_label) == ("300", "Pressure Class")
    assert (by["valve_body_type"].raw_value, by["model_no"].raw_value) == ("Globe", "667-ED")
    assert all("복합 라벨" in by[k].note for k in ("valve_body_rating", "valve_body_type"))
    # 'Cast' 는 어느 필드에도 들어가지 않는다
    assert all(r.raw_value != "Cast" for r in parsed.records)


def test_FailAirTo_는_Fail_조각을_쓴다(parsed):
    """'Fail/Air-To' = 'Close / Open' → Fail 값이 앞 조각."""
    r = parsed.by_key()["actuator_fail_action"]
    assert (r.raw_value, r.raw_label) == ("Close", "Fail")


def test_대응_필드가_없는_조각은_버린다():
    """'Design Press./Temp.' 는 규칙에 fields: [null, null] 로 명시돼 있다."""
    from src.parsers.text.composite import CompositeIndex
    rule = CompositeIndex.load().lookup("Design Press./Temp.")
    assert rule is not None
    assert rule.split("Design Press./Temp.", "27 / 430") == []


def test_스캔본은_조용히_0건을_돌려주지_않는다(tmp_path):
    """텍스트 레이어가 없으면 VLM 담당이라고 명시적으로 알린다 (원칙 5)."""
    import fitz
    from src.parsers.text.pdf_text import ScannedPdfError

    blank = tmp_path / "scanned.pdf"
    d = fitz.open()
    d.new_page(width=595, height=842)
    d.save(str(blank))
    with pytest.raises(ScannedPdfError) as e:
        parse_pdf_text(str(blank))
    assert "VLM" in str(e.value)


# ── 2단 양식에서 Normal 열이 새지 않는다 (실물 52PV014) ─────────


def _twocol_pdf(path):
    """왼쪽 라벨·값 / 오른쪽 라벨·값 2단 + 위쪽 서비스조건 블록.

    실물 `52PV014` 의 x 좌표를 그대로 옮겼다. 오른쪽 단의 라벨 열(327)이
    Normal 열(348)에서 21 밖에 안 떨어져 있어, 허용 오차가 넓으면 라벨을
    값으로 집는다.
    """
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    for x, t in [(246, "Units"), (296, "MAX."), (348, "NOR."), (403, "MIN.")]:
        page.insert_text((x, 100), t, fontsize=8)
    for x, t in [(90, "Required Cv"), (250, "Cv"), (351, "75.7")]:
        page.insert_text((x, 112), t, fontsize=8)       # 블록 안 — Nor 열이 맞다
    for x, t in [(90, "Rated Cv"), (169, "110"), (327, "Position")]:
        page.insert_text((x, 130), t, fontsize=8)       # 블록 밖 — 왼쪽 값이 맞다
    for x, t in [(90, "Fail Position"), (169, "VALVE CLOSE"),
                 (327, "Body Color"), (417, "GRAY")]:
        page.insert_text((x, 142), t, fontsize=8)
    # 스캔본 가드(문서 단위 글자 수)에 걸리지 않도록 실물만큼 본문을 채운다
    for i, y in enumerate(range(160, 400, 12)):
        page.insert_text((90, y), f"Remark {i}: text layer filler line", fontsize=8)
    doc.save(str(path))
    doc.close()
    return str(path)


def test_Normal_열은_블록_밖_행까지_따라가지_않는다(tmp_path):
    by = parse_pdf_text(_twocol_pdf(tmp_path / "twocol.pdf")).by_key()
    assert by["required_cv"].raw_value == "75.7"        # 블록 안에서는 Nor 열이 이긴다
    assert by["rated_cv"].raw_value == "110"            # 'Position' 이 아니다
    assert by["actuator_fail_action"].raw_value == "VALVE CLOSE"   # 'Body Color' 아님
