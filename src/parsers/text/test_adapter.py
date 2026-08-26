# -*- coding: utf-8 -*-
"""어댑터가 파이프라인 계약을 지키는가."""
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import schema                                             # noqa: E402
from src.contracts import (DocumentClass, PageInfo, ParserType,   # noqa: E402
                           RawExtraction, TriageResult)
from src.parsers.text.adapter import TextParser, spec_pages        # noqa: E402

BASIC = os.path.join(ROOT, "fixtures", "text", "excel_basic.xlsx")
FIELDS = schema.mvp_fields()


def _triage(path, pages=()):
    return TriageResult(source_path=path,
                        document_class=DocumentClass.DATASHEET,
                        pages=list(pages))


def test_요청한_필드를_빠짐없이_돌려준다():
    got = TextParser().extract(BASIC, _triage(BASIC), FIELDS)
    assert [r.field_key for r in got] == [f.key for f in FIELDS]
    assert all(isinstance(r, RawExtraction) for r in got)


def test_못_찾은_필드는_사유를_남긴다():
    got = {r.field_key: r for r in TextParser().extract(BASIC, _triage(BASIC), FIELDS)}
    missing = [r for r in got.values() if not r.found]
    assert missing, "이 fixture 는 MVP 필드를 전부 채우지 못한다"
    assert all(r.note for r in missing)          # 조용히 비우지 않는다


def test_담당이_아닌_확장자는_사유와_함께_비운다(tmp_path):
    tif = tmp_path / "x.tif"
    tif.write_bytes(b"")
    got = TextParser().extract(str(tif), _triage(str(tif)), FIELDS)
    assert all(not r.found and "담당이 아니다" in r.note for r in got)


def test_스캔_PDF_는_VLM_담당이라고_남긴다(tmp_path):
    import fitz
    blank = tmp_path / "scanned.pdf"
    d = fitz.open()
    d.new_page(width=595, height=842)
    d.save(str(blank))
    got = TextParser().extract(str(blank), _triage(str(blank)), FIELDS)
    assert all(not r.found and "VLM" in r.note for r in got)
    assert {r.parser for r in got} == {ParserType.PDF_TEXT}


@pytest.mark.parametrize("pages,want", [
    ([], None),
    ([PageInfo(page=3, selected=True)], [3]),
])
def test_Triage_가_고른_페이지만_본다(pages, want):
    assert spec_pages(_triage("x.pdf", pages)) == want


def test_재판독은_지원하지_않는다():
    assert TextParser().reread(BASIC, FIELDS[0], RawExtraction(field_key="x", raw_value=None)) is None
