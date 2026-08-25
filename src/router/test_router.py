# -*- coding: utf-8 -*-
"""② Router 테스트 — 포맷으로 경로를 가르고 텍스트를 버리지 않는지.

여기서 고정하는 것은 **실측으로 확인된 회귀**다.
    · 텍스트 레이어가 있어도 VLM 을 건너뛰지 않는다
      (이전 구현: 10PCV071 텍스트 경로 → 9필드 전부 N/A / VLM 경로 → 8/9)
    · 텍스트를 버리지 않고 보조 근거로 실어 보낸다
    · xls 는 xlrd, xlsx·xlsm 은 openpyxl (섞으면 읽기 실패)
    · 처리 대상이 아니면 파서를 고르지 않는다
"""
from __future__ import annotations                      # 타입 표기 일관성 유지

import json                                             # 로그 직렬화 검증

import pytest                                           # 테스트 프레임워크

from src.contracts import (
    DocumentClass, PageClass, PageInfo, ParserType, Target, TriageResult,
)
from src.router import Router, detect_format, route


def _triage(ext: str, pages, doc_class=DocumentClass.DATASHEET, target_page=None):
    """테스트용 최소 TriageResult 를 만든다."""
    return TriageResult(
        source_path=f"X-DATA SHEET_REV0{ext}",
        document_class=doc_class,
        targets=[Target(page_from=target_page, page_to=target_page)] if target_page else [],
        pages=pages,
        stats={"page_count": len(pages),
               "text_pages": sum(1 for p in pages if p.has_text_layer),
               "rendered_pages": sum(1 for p in pages if p.render_path),
               "mixed_text_scan": 0 < sum(1 for p in pages if p.has_text_layer) < len(pages)},
    )


def _page(n, *, text=0, render=True, sheet=None, spec=False):
    """테스트용 PageInfo."""
    return PageInfo(
        page=n,
        page_class=PageClass.SPEC if spec else PageClass.OTHER,
        has_text_layer=text > 0, text_len=text,
        render_path=f"/tmp/p{n}.png" if render else None,
        kind_hint=sheet or "",
    )


# ══════════════════════════════════════════════════════════════════
#  ① 포맷 → 리더 분기
# ══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("ext,family,reader", [
    (".xlsx", "excel", "openpyxl"),
    (".xlsm", "excel", "openpyxl"),
    (".xls", "excel", "xlrd"),            # openpyxl 로는 읽을 수 없다
    (".pdf", "pdf", "pymupdf"),
    (".tif", "image", "pymupdf"),
    (".tiff", "image", "pymupdf"),
    (".dwg", "", "none"),                  # 미지원
    (".docx", "", "none"),
])
def test_detect_format_picks_reader(ext, family, reader):
    """확장자로 계열과 리더를 정한다."""
    assert detect_format(f"x{ext}") == (family, reader)


# ══════════════════════════════════════════════════════════════════
#  ② 텍스트 레이어가 있어도 VLM 을 건너뛰지 않는다 — 핵심 회귀
# ══════════════════════════════════════════════════════════════════

def test_text_layer_pdf_still_goes_to_vlm():
    """텍스트가 온전한 PDF 도 VLM 으로 간다.

    실측 근거 — 이전 구현이 텍스트 경로로 보내 값을 잃었다:
        10PCV071  텍스트 경로 0/9  ·  VLM 경로 8/9
        10FV634   텍스트 경로 5/9  ·  VLM 경로 9/9
    텍스트는 글자를 보증할 뿐이고 어느 값이 어느 필드인지는 VLM 이 정한다.
    """
    d = route(_triage(".pdf", [_page(1, text=5016, spec=True)], target_page=1))
    assert d.parser is ParserType.VLM                     # 텍스트가 많아도 VLM
    assert d.routable
    assert "건너뛰지 않는다" in d.reason                    # 이유가 근거에 남는다


def test_text_is_kept_as_hint_not_discarded():
    """텍스트를 버리지 않고 보조 근거로 실어 보낸다."""
    d = route(_triage(".pdf", [_page(1, text=4063, spec=True)], target_page=1))
    unit = d.units[0]
    assert unit.has_text_layer                            # 텍스트가 있다는 사실
    assert unit.text_len == 4063                          # 양까지 전달
    assert unit.text_usable                               # 대조에 쓸 만하다고 표시
    assert d.evidence["text_hint_pages"] == 1


def test_short_text_is_not_marked_usable():
    """도장·표제만 텍스트인 스캔은 보조 근거로 쓰지 않는다 (오독 유도 방지)."""
    d = route(_triage(".pdf", [_page(1, text=40, spec=True)], target_page=1))
    assert d.units[0].has_text_layer                      # 텍스트는 있다
    assert d.units[0].text_usable is False                # 그러나 근거로는 쓰지 않는다
    assert d.evidence["text_hint_pages"] == 0


def test_scan_tif_goes_to_vlm():
    """스캔 이미지는 VLM 이다."""
    d = route(_triage(".tif", [_page(1, text=0)], target_page=1))
    assert d.parser is ParserType.VLM
    assert d.units[0].render_path                         # VLM 이 볼 이미지가 있다


# ══════════════════════════════════════════════════════════════════
#  ③ 엑셀 — 셀 좌표를 남길 수 있으므로 텍스트 파서로
# ══════════════════════════════════════════════════════════════════

def test_excel_goes_to_excel_parser_with_sheet():
    """엑셀은 EXCEL 경로이고 시트명을 처리 단위에 남긴다."""
    d = route(_triage(".xlsx", [_page(3, text=2568, sheet="TEST", spec=True)], target_page=3))
    assert d.parser is ParserType.EXCEL
    assert d.reader == "openpyxl"
    assert d.units[0].sheet == "TEST"                     # source_locator 의 근거
    assert d.units[0].page == 3


def test_xls_uses_xlrd():
    """구형 xls 는 xlrd 로 읽는다 — openpyxl 로는 열리지 않는다."""
    d = route(_triage(".xls", [_page(1, text=100, sheet="Sheet1")], target_page=1))
    assert d.parser is ParserType.EXCEL
    assert d.reader == "xlrd"


# ══════════════════════════════════════════════════════════════════
#  ④ 처리 대상 판정
# ══════════════════════════════════════════════════════════════════

def test_out_of_scope_gets_no_parser():
    """처리 대상이 아니면 파서를 고르지 않는다 — 조용히 빈 결과를 내지 않는다."""
    t = _triage(".tif", [_page(1)], doc_class=DocumentClass.OUT_OF_SCOPE)
    t.reason = "도면 — 사양표가 아님"
    d = route(t)
    assert d.parser is None
    assert d.routable is False
    assert "처리 대상이 아님" in d.reason
    assert "도면" in d.reason                              # Triage 사유를 이어 붙인다


def test_unsupported_extension_gets_no_parser():
    """미지원 포맷도 파서를 고르지 않는다."""
    d = route(_triage(".dwg", [_page(1)]))
    assert d.parser is None
    assert d.routable is False


def test_targets_narrow_the_units():
    """Triage 가 페이지를 지정하면 그 페이지만 처리 단위가 된다."""
    pages = [_page(1), _page(2, spec=True), _page(3)]
    d = route(_triage(".pdf", pages, target_page=2))
    assert [u.page for u in d.units] == [2]               # 지정된 한 장만
    assert d.evidence["unit_source"] == "triage_selected"


def test_without_targets_all_pages_become_units():
    """지정이 없으면 전 페이지를 넘긴다 — 임의로 한 장을 고르지 않는다."""
    pages = [_page(1), _page(2), _page(3)]
    d = route(_triage(".pdf", pages))
    assert [u.page for u in d.units] == [1, 2, 3]
    assert d.evidence["unit_source"] == "all_pages"


def test_triage_declined_selection_is_surfaced():
    """Triage 가 후보를 못 가려 일부러 비운 것을 근거에 드러낸다.

    판정 재료가 없어 비어 있는 것과, 사람이 골라야 해서 비워 둔 것은 다른
    상태다. 구분이 로그에 남지 않으면 처리 실패와 섞인다.
    """
    pages = [_page(1), _page(2, text=300, spec=True), _page(3, text=300, spec=True)]
    d = route(_triage(".pdf", pages))                     # targets 없음 = 고르지 않았다
    assert d.evidence["triage_declined_selection"] == [2, 3]
    assert "사람이 선택해야 한다" in d.reason
    assert d.parser is ParserType.VLM                     # 그래도 경로는 정한다


def test_single_candidate_without_target_is_not_declined():
    """후보가 1장뿐이면 "못 가림" 이 아니다 — 오탐을 만들지 않는다."""
    pages = [_page(1), _page(2, text=300, spec=True)]
    d = route(_triage(".pdf", pages))
    assert "triage_declined_selection" not in d.evidence


def test_missing_render_is_reported_not_hidden():
    """렌더가 없는 페이지는 근거에 드러낸다 — VLM 이 볼 것이 없다는 뜻이다."""
    d = route(_triage(".pdf", [_page(1, render=False)], target_page=1))
    assert d.evidence["missing_render_pages"] == [1]
    assert "렌더 없는 페이지" in d.reason


# ══════════════════════════════════════════════════════════════════
#  ⑤ 하네스 규약과 로그
# ══════════════════════════════════════════════════════════════════

def test_router_module_protocol_shape():
    """하네스가 기대하는 (ParserType, 근거, 통계) 형태를 지킨다."""
    parser, reason, stats = Router().run(
        _triage(".tif", [_page(1)], target_page=1))
    assert parser is ParserType.VLM
    assert isinstance(reason, str) and reason
    assert stats["reader"] == "pymupdf"
    assert stats["units"] == 1


def test_to_log_is_json_serializable():
    """판정 근거가 JSON 으로 나간다 (on_route_decided Hook)."""
    d = route(_triage(".pdf", [_page(1, text=200, spec=True)], target_page=1))
    body = json.dumps(d.to_log(), ensure_ascii=False)     # 예외 없이 직렬화
    assert "pymupdf" in body
    assert "text_usable" in body


def test_deterministic():
    """같은 입력 → 같은 출력 (철학 6)."""
    t = _triage(".pdf", [_page(1, text=300, spec=True)], target_page=1)
    a, b = route(t), route(t)
    assert a.to_log() == b.to_log()
