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
    assert ix.field_count == 30
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
            "note": r.note,
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


def test_복합_라벨을_쪼갠다(parsed):
    """'Body Model(Type)' = '657-ED(GLOBE)' → 모델과 바디 형상."""
    r = parsed.by_key()["model_no"]
    assert r.raw_value == "657-ED"
    assert "복합 라벨" in r.note


def test_구분자형_복합_라벨도_쪼갠다(parsed):
    r = parsed.by_key()["valve_leakage_class"]
    assert r.raw_value == "ANSI IV"          # "120 psi / ANSI IV" 의 뒤 조각
    assert r.raw_label == "Shutoff Class"


def test_스키마에_없는_조각은_버리지_않고_미매핑으로_남긴다(parsed):
    """valve_body_type 은 아직 스키마에 없다 (이종수 책임 추가 예정)."""
    pending = [u for u in parsed.unmapped if u.neighbor_value == "GLOBE"]
    assert pending and "valve_body_type" in pending[0].text


# ── 실물 배치 (fixtures/text/excel_layouts.xlsx) ───────────────
LAYOUTS = os.path.join(ROOT, "fixtures", "text", "excel_layouts.xlsx")
LAYOUTS_EXPECTED = os.path.join(ROOT, "fixtures", "text", "excel_layouts.expected.json")


@pytest.fixture(scope="module")
def layouts():
    return parse_excel(LAYOUTS)


def test_실물_배치_기대출력과_일치한다(layouts):
    with open(LAYOUTS_EXPECTED, encoding="utf-8") as f:
        expected = json.load(f)
    got = [{k: getattr(r, k) for k in
            ("field_key", "raw_value", "raw_label", "source_locator",
             "page", "confidence", "note")}
           for r in layouts.records]
    assert got == expected["records"]


def test_라벨과_값이_세_칸_떨어져도_읽는다(layouts):
    """44LV001 계열 — 항목 라벨 B열, 값 E열."""
    assert layouts.by_key()["model_no"].source_locator == "SPEC!E9"


def test_병합_라벨_바깥의_값을_읽는다():
    """11FV048 계열 — 라벨이 B:H 로 병합되고 값은 I열 (7칸 밖).

    SCAN_RIGHT 는 4 이지만 병합 범위 끝에서부터 세므로 닿는다.
    """
    by = parse_excel(LAYOUTS, sheets=["TEST"]).by_key()
    assert by["model_no"].raw_value == "657-ED"        # 'Body Model(Type)' 복합 분해
    assert by["model_no"].source_locator == "TEST!I7"
    assert by["actuator_type"].source_locator == "TEST!Z7"


def test_시트를_지정하면_그_시트만_본다():
    """Triage 가 사양표 시트를 알려주면 사진·이력 시트는 보지 않는다."""
    both = parse_excel(LAYOUTS)
    one = parse_excel(LAYOUTS, sheets=[1])
    assert {r.page for r in one.records} == {1}
    assert len(one.unmapped) < len(both.unmapped)
    assert parse_excel(LAYOUTS, sheets=["SPEC"]).by_key().keys() == one.by_key().keys()


def test_매핑되는_라벨이_값을_먼저_가져간다(layouts):
    """스키마에 없는 텍스트가 값을 채가면 진짜 라벨이 굶는다."""
    locs = {r.source_locator for r in layouts.records}
    assert "SPEC!E35" in locs                # Fail Position 의 값
    # 같은 셀을 두 라벨이 값으로 쓰지 않는다
    assert len(locs) == len({r.source_locator for r in layouts.records if r.found})


def test_xls_는_xlrd_경로로_보낸다(monkeypatch, tmp_path):
    """계약상 ParserType.EXCEL 은 xls 를 포함한다(src/contracts.py).
    openpyxl 은 .xls 를 못 읽으므로 확장자로 갈라 xlrd 경로를 타야 한다."""
    import openpyxl

    from src.parsers.text import excel as excel_mod

    made = openpyxl.Workbook()
    ws = made.active
    ws.title = "SPEC"
    ws["A1"], ws["B1"] = "Manufacturer", "FISHER"
    ws["A2"], ws["B2"] = "Model No.", "667-ED"

    called = []
    monkeypatch.setattr(excel_mod, "load_xls", lambda p: (called.append(p), made)[1])

    path = tmp_path / "old.xls"
    path.write_bytes(b"")                       # 내용은 load_xls 가 만든 것으로 대체된다
    by = excel_mod.parse_excel(str(path)).by_key()

    assert called == [str(path)]                # openpyxl 로 열지 않았다
    assert by["manufacturer"].raw_value == "FISHER"
    assert by["model_no"].raw_value == "667-ED"


@pytest.mark.skipif(__import__("importlib").util.find_spec("xlwt") is None,
                    reason="xlwt 미설치 — 구형 xls 를 만들 수 없다")
def test_구형_xls_왕복(tmp_path):
    """실제 .xls 왕복. xlwt 가 있을 때만 돈다.
    (설치 없이도 raw_file 의 실물 .xls 로 수동 확인함)"""
    import xlwt

    from src.parsers.text.xls_compat import load_xls

    path = tmp_path / "old.xls"
    wbx = xlwt.Workbook()
    sh = wbx.add_sheet("SPEC")
    sh.write(0, 0, "Manufacturer")
    sh.write(0, 1, "FISHER")
    wbx.save(str(path))

    assert load_xls(str(path)).sheetnames == ["SPEC"]
    assert parse_excel(str(path)).by_key()["manufacturer"].raw_value == "FISHER"
