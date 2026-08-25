# -*- coding: utf-8 -*-
"""조립 검증 — `build()` 가 실제 모듈을 꽂는가.

■ 왜 이 테스트가 있나 (2026-08-25)
────────────────────────────────────────────────────────────────────
`Pipeline` 의 각 슬롯은 기본 구현(stub)을 갖고 있다. 그래서 **조립을 잊어도
아무 오류 없이 돌아간다** — 전 필드가 N/A 로 나올 뿐이다. 실제로 오늘까지
조립부 두 곳(CLI·화면)에 모듈 주입이 0개인 상태로 있었고, 누구도 실패를
보지 못했다.

그 실수를 두 번 하지 않기 위한 테스트다. "무엇이 꽂혀 있는가" 를 못으로 박는다.
"""
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.pipeline import DefaultRouter, DefaultTriage, NullParser, build  # noqa: E402


@pytest.fixture(scope="module")
def assembled():
    notes: list[str] = []
    return build(notes=notes), notes


def test_기본_구현이_남아_있지_않다(assembled):
    """하나라도 stub 이 남아 있으면 그 구간은 조용히 N/A 를 낸다."""
    p, _ = assembled
    assert not isinstance(p.triage, DefaultTriage)
    assert not isinstance(p.router, DefaultRouter)
    assert not isinstance(p.text_parser, NullParser)
    assert not isinstance(p.format_validator, type(None))


def test_꽂힌_모듈이_실제_구현이다(assembled):
    p, _ = assembled
    assert type(p.triage).__module__.startswith("src.triage")
    assert type(p.router).__module__.startswith("src.router")
    assert type(p.text_parser).__module__ == "src.parsers.text.adapter"
    assert type(p.format_validator).__module__ == "src.validate.format.adapter"


def test_VLM_은_대조_래퍼로_감싼다(assembled):
    """CLAUDE.md — 텍스트는 글자를 보증할 뿐이고 필드 배정은 VLM 이 정한다.

    래퍼가 두 경로를 다 돌리고, 값이 다르면 확신도를 0 으로 내려 사람에게 보낸다.
    VLM 을 구성할 수 없는 환경이면 stub 이 남고 사유가 메모에 적힌다.
    """
    p, notes = assembled
    if isinstance(p.vlm_parser, NullParser):
        assert any("VLM 파서 미구성" in n for n in notes), notes
        return
    assert type(p.vlm_parser).__name__ == "DualParser"
    assert p.vlm_parser.vlm is not None and p.vlm_parser.text is not None


def test_대조를_끄면_VLM_단독이_된다():
    p = build(dual=False)
    if not isinstance(p.vlm_parser, NullParser):
        assert type(p.vlm_parser).__name__ != "DualParser"


def test_VLM_을_끄면_슬롯을_비운다():
    """베이스라인 ② 측정용. PDF 는 텍스트 경로로 강제된다(run_document)."""
    p = build(use_vlm=False)
    assert isinstance(p.vlm_parser, NullParser)
    assert p.use_vlm is False


def test_조립_실패를_삼키지_않는다():
    """꽂지 못한 모듈이 있으면 사유가 남아야 한다 (철학 5).

    메모가 비어 있다는 것은 "전부 꽂혔다" 는 뜻이고, 그 경우에만 비어야 한다.
    """
    notes: list[str] = []
    p = build(notes=notes)
    stubbed = [name for name, mod in (("triage", p.triage), ("router", p.router),
                                      ("vlm", p.vlm_parser))
               if type(mod).__module__ == "src.pipeline"]
    assert bool(stubbed) == bool(notes), (stubbed, notes)


def test_엑셀은_조립만으로_값이_나온다():
    """Router 가 엑셀을 텍스트 경로로 보내므로 VLM 없이도 값이 나와야 한다.

    조립 전에는 같은 파일에서 9필드 전부 N/A 였다.
    """
    path = os.path.join(ROOT, "raw_file", "11FV048-DATA SHEET_REV2.xlsx")
    if not os.path.exists(path):
        pytest.skip("회사 문서는 저장소에 없다 (raw_file 은 .gitignore)")
    doc = build(use_vlm=False).run_document(path)
    got = [r for r in doc.records if r.value]
    assert len(got) >= 4, [(r.field_key, r.value, r.note) for r in doc.records]
    assert any(r.field_key == "engineering_tag_no" for r in got)
