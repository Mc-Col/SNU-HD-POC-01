# -*- coding: utf-8 -*-
"""① Triage 자체 검증 — 입력을 만들어 돌리고 기대 출력과 맞춘다

    python fixtures/triage/test_triage.py

## 입력을 왜 코드로 만드는가

`raw_file/` 의 회사 문서 1,059건은 git 추적 대상이 아니다(CLAUDE.md 절대 금지).
그래서 입력을 **합성해서** 만든다. 입력의 명세(`CASES`)와 기대 출력이 이 파일에
함께 있으므로, 남의 모듈이나 실물 데이터를 기다리지 않고 검증된다(철학 3).

## 이 파일이 잠그는 것

    · 최신성으로 고른다 — 태그 단독성으로 고르지 않는다 (10FV011 실물 함정)
    · 폐기 표기(OLD)가 있는 후보는 제외한다
    · 후보를 못 가리면 **고르지 않는다** — 실물 6건이 이 경로 (070100 등)
    · 정비·개조 보고서를 배제하지 않는다 (실물 110건 오배제 회귀)
    · 예외를 던지지 않는다 — 미지원 포맷도 정상 반환
    · 같은 입력 → 같은 출력 (철학 6)
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.stdout.reconfigure(encoding="utf-8")

from src.contracts import DocumentClass                  # noqa: E402
from src.triage import triage                            # noqa: E402

ok = fail = 0


def check(label: str, got, want) -> None:
    """기대와 실제를 비교해 세고 출력한다."""
    global ok, fail
    if got == want:
        ok += 1
        print(f"  OK   {label}")
    else:
        fail += 1
        print(f"  실패 {label}\n         받음 {got!r}\n         기대 {want!r}")


def check_in(label: str, needle: str, haystack: str) -> None:
    """문구가 포함되어 있는지 본다 (판정 사유 검증용)."""
    global ok, fail
    if needle in haystack:
        ok += 1
        print(f"  OK   {label}")
    else:
        fail += 1
        print(f"  실패 {label}\n         '{needle}' 없음. 받은 사유: {haystack[:160]!r}")


# ══════════════════════════════════════════════════════════════════
#  입력 조각 — 실물 표기에서 가져온 문구만 쓴다
# ══════════════════════════════════════════════════════════════════

def spec_text(date: str, tags: str = "10-FV-011", marker: str = "") -> str:
    """사양표 페이지 텍스트를 만든다 (판정 임계값을 넘도록 반복한다)."""
    head = (f"CONTROL VALVE SPECIFICATIONS\n"
            f"Tag No. {tags}\nModel No. 667-ED\nRated Cv 95\n"
            f"Body Size 3in\nRating ANSI CLASS 600\nDate {date}\n"
            f"Body WC5\nAir Fails Valve to Close\n{marker}\n")
    return head * 3                                       # 100자 임계를 확실히 넘긴다


COVER = "TRANSMITTAL\nTo: Kukdong Oil\nAttached: 1 sheet\nRef: PO-1988\n" * 3

# 각 항목: (파일명, 페이지별 텍스트) — 텍스트가 빈 문자열이면 스캔 페이지다
CASES: dict[str, list[str]] = {
    # ① 표지 + 사양표 → 사양표 한 장을 고른다
    "10FV011-DATA SHEET_REV1.pdf": [COVER, spec_text("2003/03/25")],
    # ② 한 장짜리 사양표
    "10FV002-DATA SHEET_REV0.pdf": [spec_text("2015/07/01")],
    # ③ 정비·개조 보고서 — 대상이고 주의 문구가 붙는다
    "12LV014-RETROFIT REPORT_REV0.pdf": [COVER, spec_text("2011/03/18"), COVER],
    # ④ 후보 2장, 날짜 동일 → 못 가린다 (실물 070100 형태)
    "070100_REV0.pdf": [COVER,
                        spec_text("2007/02/17", "B10-TV-040"),
                        spec_text("2007/02/17", "B10-TV-1016")],
    # ⑤ 실물 10FV011 함정 — 2003 Retrofit(태그 4개) vs 1986 원본(단독 태그, OLD)
    "10FV012-DATA SHEET_REV1.pdf": [
        spec_text("2003/03/25", "10-FV-011, 10-FV-012, 10-FV-013, 10-FV-014", "RETROFIT"),
        spec_text("1986/09/06", "10-FV-011", "OLD")],
    # ⑥ 도면 — 파일명으로 제외
    "14FV001-DRAWING_REV0.pdf": [spec_text("2003/03/25")],
    # ⑦ 미지원 포맷 — 예외가 아니라 UNSUPPORTED
    "19PCV005-DATA SHEET_REV0.doc": [],
    # ⑧ 텍스트 0자 스캔 — 코퍼스의 85.2%. 페이지를 고르지 않는다
    "10FV002-DATA SHEET_REV0.tif": [],
}


def build(work: str) -> dict[str, str]:
    """`CASES` 명세대로 입력 파일을 만든다. 반환: 파일명 → 경로."""
    import pymupdf                                        # 렌더·생성 공용 의존

    made: dict[str, str] = {}
    for name, texts in CASES.items():
        path = os.path.join(work, name)
        if name.lower().endswith(".tif"):                 # 텍스트 0자 스캔본
            from PIL import Image
            # 코퍼스 실측과 같은 1비트 이진 이미지 · 150dpi A4 상당
            Image.new("1", (1240, 1753), color=1).save(path)
            made[name] = path
            continue
        if not name.lower().endswith(".pdf"):             # 미지원 포맷은 내용이 필요 없다
            with open(path, "wb") as fp:
                fp.write(b"not a real document")
            made[name] = path
            continue
        doc = pymupdf.open()
        for text in texts:
            page = doc.new_page(width=595, height=842)    # A4
            if text:
                page.insert_text((50, 60), text, fontsize=8)
        doc.save(path)
        doc.close()
        made[name] = path
    return made


# ══════════════════════════════════════════════════════════════════
#  검증
# ══════════════════════════════════════════════════════════════════

def main() -> int:
    work = tempfile.mkdtemp(prefix="fx_triage_")
    render = os.path.join(work, "render")
    try:
        made = build(work)

        print("\n[1] 사양표 한 장을 골라 타깃으로 넘긴다")
        r = triage(made["10FV011-DATA SHEET_REV1.pdf"], render_dir=render)
        check("분류 = datasheet_embedded", r.document_class, DocumentClass.DATASHEET_EMBEDDED)
        check("선택 페이지 = p2", r.selected_page.page if r.selected_page else None, 2)
        check("타깃 1건", len(r.targets), 1)
        check("타깃 페이지 = 2", r.targets[0].page_from, 2)
        check("expected_tag_count = 1 (MVP 1파일=1자산)", r.targets[0].expected_tag_count, 1)
        check("전 페이지 렌더", all(p.render_path for p in r.pages), True)
        check("파일명 태그", r.file_tag, "A10FV011")
        check("문서종류", r.file_doc_kind, "DATA SHEET")
        check("rev", r.file_rev, "REV1")

        print("\n[2] 한 장짜리 사양표")
        r = triage(made["10FV002-DATA SHEET_REV0.pdf"], render_dir=render)
        check("분류 = datasheet", r.document_class, DocumentClass.DATASHEET)
        check("선택 페이지 = p1", r.selected_page.page if r.selected_page else None, 1)

        print("\n[3] 정비·개조 보고서는 대상이고 주의 문구가 붙는다")
        r = triage(made["12LV014-RETROFIT REPORT_REV0.pdf"], render_dir=render)
        check("처리 대상", r.processable, True)
        check("분류 = datasheet_embedded", r.document_class, DocumentClass.DATASHEET_EMBEDDED)
        check("선택 페이지 = p2", r.selected_page.page if r.selected_page else None, 2)
        check_in("사양 칸 경고", "비어 있을 수 있음", r.reason)

        print("\n[4] 후보를 못 가리면 고르지 않는다 (지시서 118행)")
        r = triage(made["070100_REV0.pdf"], render_dir=render)
        check("후보 2장 인식", r.stats["spec_pages"], 2)
        check("선택 없음", r.selected_page, None)
        check("타깃 없음", r.targets, [])
        check("선택 표시된 페이지 없음", any(p.selected for p in r.pages), False)
        check_in("사유에 '고르지 않는다'", "고르지 않는다", r.reason)
        check("자산 N건 근거 남김", len(r.extra_assets) > 0, True)
        check("렌더는 채운다 (사람이 화면에서 고른다)",
              all(p.render_path for p in r.pages), True)

        print("\n[5] 최신성으로 고른다 — 태그 단독성이 아니다 (10FV011 함정)")
        r = triage(made["10FV012-DATA SHEET_REV1.pdf"], render_dir=render)
        check("2003 Retrofit 쪽(p1)을 고른다",
              r.selected_page.page if r.selected_page else None, 1)
        check("1986 OLD 쪽(p2)은 폐기 표기로 제외", r.pages[1].superseded, True)

        print("\n[6] 도면은 파일명으로 제외하고 사유를 남긴다")
        r = triage(made["14FV001-DRAWING_REV0.pdf"], render_dir=render)
        check("분류 = out_of_scope", r.document_class, DocumentClass.OUT_OF_SCOPE)
        check("사유 비어 있지 않음", bool(r.reason.strip()), True)
        check("처리 대상 아님", r.processable, False)

        print("\n[7] 미지원 포맷도 예외를 던지지 않는다")
        r = triage(made["19PCV005-DATA SHEET_REV0.doc"], render_dir=render)
        check("분류 = unsupported", r.document_class, DocumentClass.UNSUPPORTED)
        check("사유 있음", bool(r.reason.strip()), True)

        print("\n[8] 없는 파일도 예외를 던지지 않는다")
        r = triage(os.path.join(work, "없는파일-DATA SHEET_REV0.pdf"), render_dir=render)
        check("반환됨", r.document_class is not None, True)
        check("사유 있음", bool(r.reason.strip()), True)

        print("\n[9] 텍스트 0자 스캔 — 페이지를 고르지 않는다 (지시서 91·118행)")
        r = triage(made["10FV002-DATA SHEET_REV0.tif"], render_dir=render)
        check("처리 대상으로 통과", r.processable, True)
        check("분류 = datasheet (파일명 근거)", r.document_class, DocumentClass.DATASHEET)
        check("사양표라 단정하지 않음", r.stats["spec_pages"], 0)
        check("선택 없음", r.selected_page, None)
        check("타깃 없음", r.targets, [])
        check("선택 표시된 페이지 없음", any(p.selected for p in r.pages), False)
        check("렌더는 채운다 (VLM 이 볼 이미지)",
              all(p.render_path for p in r.pages), True)
        check("확신도 0.5 미만", r.confidence < 0.5, True)
        check_in("사유에 '고르지 않는다'", "고르지 않는다", r.reason)

        print("\n[10] 같은 입력 → 같은 출력 (철학 6)")
        a = triage(made["10FV011-DATA SHEET_REV1.pdf"], render_dir=os.path.join(work, "r1"))
        b = triage(made["10FV011-DATA SHEET_REV1.pdf"], render_dir=os.path.join(work, "r2"))
        check("분류 동일", a.document_class, b.document_class)
        check("확신도 동일", a.confidence, b.confidence)
        check("선택 페이지 동일",
              a.selected_page.page if a.selected_page else None,
              b.selected_page.page if b.selected_page else None)
        check("사유 동일", a.reason, b.reason)
    finally:
        shutil.rmtree(work, ignore_errors=True)           # 임시 입력을 남기지 않는다

    print("\n" + "=" * 62)
    print(f"  통과 {ok} / 실패 {fail}")
    print("=" * 62)
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
