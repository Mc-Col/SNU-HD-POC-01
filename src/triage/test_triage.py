# -*- coding: utf-8 -*-
"""① Triage 테스트 — 공용 모듈 조립이 의도대로 판정하는지.

fixture 는 전부 코드로 생성한 합성 데이터다. raw_file 의 회사 문서를 복사하지 않는다
(`fixtures/` 는 git 추적 대상이라 커밋되어 버린다).

여기서 고정하는 것은 **한 번 틀렸던** 판정들이다.
    · 정비·개조 보고서를 배제하지 않는다 (실측 110건 오배제, 골든 d006 포함)
    · 태그 단독성이 아니라 최신성으로 페이지를 고른다 (10FV011 폐기본 선택)
    · 스캔 문서에서도 render_path 를 채운다 (화면이 71.9% 를 표시하는 유일한 경로)
    · 예외를 던지지 않는다
"""
from __future__ import annotations                      # 타입 표기 일관성 유지

import pytest                                           # 테스트 프레임워크

from src.contracts import DocumentClass, PageClass
from src.triage import match_filename_pattern, probe_structure, triage


# ══════════════════════════════════════════════════════════════════
#  ① 파일명 판정 — 공용 DOC_KINDS 를 따르는가
# ══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("stem,in_scope,kind", [
    ("10FV002-DATA SHEET_REV0", True, "DATA SHEET"),
    ("30PV006-SPECIFICATION DATA SHEET_REV0", True, "SPECIFICATION DATA SHEET"),
    # ── 정비·개조 보고서는 대상이다 (2026-08-24 결정, 앞선 제외 판단 철회) ──
    ("10PV018-REPAIR REPORT_REV1", True, "REPAIR REPORT"),
    ("12LV014-RETROFIT REPORT_REV0", True, "RETROFIT REPORT"),
    ("17FV030-TEST REPORT_REV0", True, "TEST REPORT"),
    # ── 이것만 제외다 ──
    ("14FV001-DRAWING_REV0", False, "DRAWING"),
    ("CPC-INSTRUMENT LIST _REV0", False, "INSTRUMENT LIST"),
])
def test_filename_scope_follows_shared_doc_kinds(stem, in_scope, kind):
    """파일명 판정이 공용 DOC_KINDS 와 일치한다 (자체 키워드 목록을 두지 않는다)."""
    info = match_filename_pattern(f"{stem}.tif")
    assert info.doc_kind == kind                        # 문서종류 표시명
    assert info.in_scope is in_scope                    # 대상 여부


def test_report_kinds_are_not_excluded():
    """정비·개조 보고서가 배제되지 않는다 — 이 회귀가 골든 d006 을 잃게 했다."""
    from src import preprocess
    for kind in preprocess.REPORT_KINDS:                # REPAIR·RETROFIT·TEST REPORT
        assert preprocess.scope_reason(f"X-{kind}_REV0.pdf") == ""   # 제외 사유가 없다


def test_report_kinds_carry_caution():
    """대상이지만 "사양 칸이 비어 있을 수 있음" 경고가 함께 나온다."""
    from src import preprocess
    note = preprocess.caution_reason("10PV018-REPAIR REPORT_REV1.xlsx")
    assert note                                          # 경고가 있다
    assert "비어 있을 수 있음" in note                     # 억지로 채우지 않도록 알린다


# ══════════════════════════════════════════════════════════════════
#  ② 미지원·예외 — 던지지 않는다
# ══════════════════════════════════════════════════════════════════

def test_unsupported_format_returns_normally(tmp_path):
    """미지원 포맷은 예외가 아니라 UNSUPPORTED 로 돌아온다."""
    p = tmp_path / "19PCV005-DATA SHEET_REV0.doc"
    p.write_bytes(b"not a real doc")
    result = triage(str(p))
    assert result.document_class is DocumentClass.UNSUPPORTED
    assert result.reason                                 # 사유가 비어 있지 않다
    assert result.processable is False


def test_missing_file_returns_normally(tmp_path):
    """없는 파일도 예외를 던지지 않는다 (철학 5 — 실패를 삼키지 않되 죽지 않는다)."""
    result = triage(str(tmp_path / "없는파일-DATA SHEET_REV0.pdf"))
    assert result.document_class in (
        DocumentClass.DATASHEET, DocumentClass.OUT_OF_SCOPE, DocumentClass.UNSUPPORTED)
    assert result.reason                                 # 무슨 일이 있었는지 남는다


def test_out_of_scope_always_has_reason(tmp_path):
    """out_of_scope 는 reason 이 필수다 (계약 요구)."""
    p = tmp_path / "14FV001-DRAWING_REV0.tif"
    p.write_bytes(b"x")
    result = triage(str(p))
    assert result.document_class is DocumentClass.OUT_OF_SCOPE
    assert result.reason.strip()                         # 비어 있으면 계약 위반


# ══════════════════════════════════════════════════════════════════
#  ③ 페이지 판정 — 합성 PDF 로
# ══════════════════════════════════════════════════════════════════

def _make_pdf(path, pages_text):
    """텍스트 레이어가 있는 합성 PDF 를 만든다 (pages_text 는 페이지별 문자열)."""
    import pymupdf
    doc = pymupdf.open()
    for text in pages_text:
        page = doc.new_page(width=595, height=842)       # A4
        if text:
            page.insert_text((50, 60), text, fontsize=9)
        doc.save(str(path)) if False else None
    doc.save(str(path))
    doc.close()


SPEC_TEXT = (
    "CONTROL VALVE SPECIFICATIONS\n"
    "Tag No. 10-FV-011\nModel No. 667-ED\nRated Cv 95\n"
    "Body Size 3in\nRating ANSI CLASS 600\nDate 2003/03/25\n"
    "Body WC5\nAir Fails Valve to Close\n" * 3          # 판정 임계값(120자)을 넘긴다
)
COVER_TEXT = "TRANSMITTAL\nTo: Kukdong Oil\nAttached: 1 sheet\n" * 3


def test_spec_page_is_detected_and_selected(tmp_path):
    """사양표 머리글이 있는 페이지를 찾아 선택하고 render_path 를 채운다."""
    p = tmp_path / "10FV011-DATA SHEET_REV1.pdf"
    _make_pdf(p, [COVER_TEXT, SPEC_TEXT])                # 1p 표지 · 2p 사양표
    result = triage(str(p), render_dir=str(tmp_path / "render"))

    assert result.processable                             # 처리 대상
    assert result.selected_page is not None               # 한 장을 골랐다
    assert result.selected_page.page == 2                 # 사양표 쪽이다
    assert result.selected_page.page_class is PageClass.SPEC
    assert result.selected_page.render_path                # 화면이 쓸 이미지가 있다
    assert result.targets and result.targets[0].page_from == 2   # 하류가 볼 타깃


def test_all_pages_get_render_path(tmp_path):
    """선택되지 않은 페이지도 render_path 를 갖는다 — 화면이 "왜 빠졌나" 를 보여준다."""
    p = tmp_path / "10FV011-DATA SHEET_REV1.pdf"
    _make_pdf(p, [COVER_TEXT, SPEC_TEXT, COVER_TEXT])
    result = triage(str(p), render_dir=str(tmp_path / "render"))
    assert len(result.pages) == 3
    assert all(pg.render_path for pg in result.pages)     # 전 페이지 렌더
    assert result.stats["rendered_pages"] == 3


def test_embedded_when_spec_is_one_of_many(tmp_path):
    """사양표가 여러 페이지 중 일부면 DATASHEET_EMBEDDED 다."""
    p = tmp_path / "12LV014-RETROFIT REPORT_REV0.pdf"
    _make_pdf(p, [COVER_TEXT, SPEC_TEXT, COVER_TEXT])
    result = triage(str(p), render_dir=str(tmp_path / "render"))
    assert result.document_class is DocumentClass.DATASHEET_EMBEDDED
    assert "비어 있을 수 있음" in result.reason             # 보고서 경고가 함께 남는다


def test_no_text_falls_back_to_vlm_judgement(tmp_path):
    """텍스트가 없으면(스캔) 임의로 사양표라 하지 않고 VLM 판정으로 넘긴다."""
    from PIL import Image
    p = tmp_path / "10FV002-DATA SHEET_REV0.tif"
    Image.new("1", (1240, 1753), color=1).save(p)         # 빈 스캔 (텍스트 0)
    result = triage(str(p), render_dir=str(tmp_path / "render"))

    assert result.document_class is DocumentClass.DATASHEET      # 파일명 근거로 통과
    assert result.stats["spec_pages"] == 0                       # 사양표라 단정하지 않았다
    assert result.pages[0].page_class is PageClass.OTHER         # 판정 보류
    assert result.pages[0].render_path                           # VLM 이 볼 이미지는 있다
    assert result.confidence < 0.5                               # 근거가 약하다고 표시
    assert "VLM" in result.reason                                # 누가 판정할지 남긴다


def test_ambiguous_candidates_are_not_picked(tmp_path):
    """사양표 후보가 2장인데 날짜가 같으면 **고르지 않는다.**

    지시서 118행 — "못 가릴 때 아무거나 고르지 말 것. None + 사유가 정답이다."
    실물 6건이 이 경로다. `070100_REV0.pdf` 는 서로 다른 밸브 2대가 든 견적서라
    한 장을 고르면 다른 설비의 값을 이 태그의 값으로 만든다.
    """
    p = tmp_path / "070100_REV0.pdf"
    same_date = SPEC_TEXT.replace("2003/03/25", "2007/02/17")
    _make_pdf(p, [COVER_TEXT, same_date, same_date])      # 후보 2장, 날짜 동일
    result = triage(str(p), render_dir=str(tmp_path / "render"))

    assert result.stats["spec_pages"] == 2                # 후보를 2장 찾았다
    assert result.selected_page is None                   # 그러나 고르지 않았다
    assert result.targets == []                           # 하류에 페이지를 지정하지 않는다
    assert not any(pg.selected for pg in result.pages)    # 어느 페이지도 선택 표시 없음
    assert result.confidence < 0.5                        # 근거가 약하다고 표시
    assert "고르지 않는다" in result.reason                 # 왜 비었는지 남는다
    assert "p2" in result.reason and "p3" in result.reason  # 후보가 어디였는지


def test_ambiguous_case_still_renders_every_page(tmp_path):
    """고르지 않아도 렌더는 채운다 — 사람이 화면에서 골라야 하기 때문이다."""
    p = tmp_path / "070100_REV0.pdf"
    same_date = SPEC_TEXT.replace("2003/03/25", "2007/02/17")
    _make_pdf(p, [COVER_TEXT, same_date, same_date])
    result = triage(str(p), render_dir=str(tmp_path / "render"))
    assert all(pg.render_path for pg in result.pages)     # 3장 모두 이미지가 있다


def test_thresholds_come_from_shared_preprocess():
    """임계값을 자체로 두지 않는다 — CLAUDE.md 하지 말 것.

    "임계값을 하드코딩하지 말 것 — `preprocess.TEXT_LAYER_MIN` (100자)."
    """
    from src import preprocess
    from src.router.constants import TEXT_HINT_MIN_CHARS
    from src.triage.constants import MIN_TEXT_FOR_SPEC_JUDGEMENT
    assert MIN_TEXT_FOR_SPEC_JUDGEMENT == preprocess.TEXT_LAYER_MIN
    assert TEXT_HINT_MIN_CHARS == preprocess.TEXT_LAYER_MIN


def test_probe_structure_reports_mixed_text_scan(tmp_path):
    """텍스트·스캔 혼재를 stats 에 표시한다 — Router 가 페이지 단위로 판단해야 한다."""
    p = tmp_path / "070055_REV0.pdf"
    _make_pdf(p, ["", SPEC_TEXT, ""])                     # 2페이지만 텍스트
    _, stats = probe_structure(str(p))
    assert stats["page_count"] == 3
    assert stats["text_pages"] == 1
    assert stats["mixed_text_scan"] is True


def test_deterministic(tmp_path):
    """같은 입력 → 같은 출력 (철학 6)."""
    p = tmp_path / "10FV011-DATA SHEET_REV1.pdf"
    _make_pdf(p, [COVER_TEXT, SPEC_TEXT])
    a = triage(str(p), render_dir=str(tmp_path / "r1"))
    b = triage(str(p), render_dir=str(tmp_path / "r2"))
    assert a.document_class is b.document_class
    assert a.confidence == b.confidence
    assert [pg.page_class for pg in a.pages] == [pg.page_class for pg in b.pages]
    assert (a.selected_page.page if a.selected_page else None) == \
           (b.selected_page.page if b.selected_page else None)
