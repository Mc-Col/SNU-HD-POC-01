# -*- coding: utf-8 -*-
"""② Router 자체 검증 — Triage 출력을 받아 경로를 고른다

    python fixtures/router/test_router.py

## 입력이 무엇인가

Router 의 입력은 파일이 아니라 **① Triage 의 출력**이다. 그래서 이 fixture 는
합성 파일을 만들어 `triage()` 를 먼저 돌리고, 그 결과를 `route()` 에 넣는다.
① → ② 인터페이스가 실제로 맞물리는지까지 함께 검증된다.

## 이 파일이 잠그는 것

    · 엑셀은 포맷별 리더 분기 (xlsx·xlsm → openpyxl, xls → xlrd)
    · 텍스트 레이어가 온전한 PDF 도 VLM 으로 간다 (실측 10PCV071 0/9 vs 8/9)
    · 텍스트를 버리지 않고 보조 근거로 실어 보낸다
    · Triage 가 페이지를 고르지 않았으면 그 사실이 근거에 남는다
    · 처리 대상이 아니면 파서를 고르지 않는다
    · 같은 입력 → 같은 출력 (철학 6)
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.stdout.reconfigure(encoding="utf-8")

from src import preprocess                               # noqa: E402  공용 임계값 대조
from src.contracts import ParserType                     # noqa: E402
from src.router import detect_format, route              # noqa: E402
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
    """문구가 포함되어 있는지 본다."""
    global ok, fail
    if needle in haystack:
        ok += 1
        print(f"  OK   {label}")
    else:
        fail += 1
        print(f"  실패 {label}\n         '{needle}' 없음. 받음: {haystack[:160]!r}")


SPEC = ("CONTROL VALVE SPECIFICATIONS\n"
        "Tag No. 10-FV-011\nModel No. 667-ED\nRated Cv 95\n"
        "Body Size 3in\nRating ANSI CLASS 600\nDate 2003/03/25\n") * 3
COVER = "TRANSMITTAL\nTo: Kukdong Oil\nAttached: 1 sheet\n" * 3


def make_pdf(path: str, texts: list[str]) -> str:
    """텍스트 레이어가 있는 합성 PDF 를 만든다."""
    import pymupdf
    doc = pymupdf.open()
    for text in texts:
        page = doc.new_page(width=595, height=842)
        if text:
            page.insert_text((50, 60), text, fontsize=8)
    doc.save(path)
    doc.close()
    return path


def make_tif(path: str) -> str:
    """텍스트가 없는 1비트 스캔본을 만든다 (코퍼스 실측과 같은 형식)."""
    from PIL import Image
    Image.new("1", (1240, 1753), color=1).save(path)      # 150dpi A4 상당
    return path


def make_xlsx(path: str, sheets: dict[str, list[list[str]]]) -> str:
    """시트별 셀 값을 가진 합성 엑셀을 만든다."""
    import openpyxl
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(title=name)
        for row in rows:
            ws.append(row)
    wb.save(path)
    return path


def main() -> int:
    work = tempfile.mkdtemp(prefix="fx_router_")
    render = os.path.join(work, "render")
    try:
        print("\n[1] 확장자 → 계열·리더 분기")
        for ext, want in [(".xlsx", ("excel", "openpyxl")), (".xlsm", ("excel", "openpyxl")),
                          (".xls", ("excel", "xlrd")), (".pdf", ("pdf", "pymupdf")),
                          (".tif", ("image", "pymupdf")), (".tiff", ("image", "pymupdf")),
                          (".dwg", ("", "none")), (".docx", ("", "none"))]:
            check(f"{ext} → {want}", detect_format(f"x{ext}"), want)

        print("\n[2] 텍스트가 온전한 PDF 도 VLM 으로 간다")
        p = make_pdf(os.path.join(work, "10FV634-DATA SHEET_REV0.pdf"), [SPEC])
        d = route(triage(p, render_dir=render))
        check("파서 = VLM", d.parser, ParserType.VLM)
        check("리더 = pymupdf", d.reader, "pymupdf")
        check("처리 가능", d.routable, True)
        check_in("근거에 이유", "건너뛰지 않는다", d.reason)

        print("\n[3] 텍스트를 버리지 않고 보조 근거로 실어 보낸다")
        unit = d.units[0]
        check("텍스트 레이어 사실 전달", unit.has_text_layer, True)
        check("글자 수 전달", unit.text_len > preprocess.TEXT_LAYER_MIN, True)
        check("보조 근거로 쓸 만하다고 표시", unit.text_usable, True)
        check("보조 페이지 수 집계", d.evidence["text_hint_pages"], 1)
        check("VLM 이 볼 이미지 있음", bool(unit.render_path), True)

        print("\n[4] 스캔 tif — 텍스트 없이 이미지로만")
        p = make_tif(os.path.join(work, "10FV002-DATA SHEET_REV0.tif"))
        d = route(triage(p, render_dir=render))
        check("파서 = VLM", d.parser, ParserType.VLM)
        check("텍스트 보조 없음", d.evidence["text_hint_pages"], 0)
        check("렌더는 있다", bool(d.units[0].render_path), True)

        print("\n[5] 엑셀 — 리더 분기와 시트명 전달")
        p = make_xlsx(os.path.join(work, "10PV018-REPAIR REPORT_REV1.xlsx"),
                      {"COVER": [["TRANSMITTAL"]],
                       "TEST": [["CONTROL VALVE SPECIFICATIONS"], ["Tag No.", "10-PV-018"],
                                ["Model No.", "667-ED"], ["Rated Cv", 195],
                                ["Body Size", "3in"], ["Rating", "ANSI CLASS 600"],
                                ["Date", "2011/03/18"], ["Body", "WC5"],
                                ["Air Fails Valve to", "Close"]]})
        d = route(triage(p, render_dir=render))
        check("파서 = EXCEL", d.parser, ParserType.EXCEL)
        check("리더 = openpyxl", d.reader, "openpyxl")
        check("시트명 전달", d.units[0].sheet, "TEST")

        print("\n[6] Triage 가 고르지 않았으면 근거에 남는다")
        p = make_pdf(os.path.join(work, "070100_REV0.pdf"),
                     [COVER, SPEC.replace("2003/03/25", "2007/02/17"),
                      SPEC.replace("2003/03/25", "2007/02/17")])
        tri = triage(p, render_dir=render)
        check("Triage 가 고르지 않음", tri.targets, [])
        d = route(tri)
        check("후보 페이지를 근거에 남김", d.evidence["triage_declined_selection"], [2, 3])
        check_in("사람이 골라야 한다고 남김", "사람이 선택해야 한다", d.reason)
        check("처리 단위는 전 페이지", [u.page for u in d.units], [1, 2, 3])
        check("근거에 표시", d.evidence["unit_source"], "all_pages")

        print("\n[7] 처리 대상이 아니면 파서를 고르지 않는다")
        p = make_tif(os.path.join(work, "14FV001-DRAWING_REV0.tif"))
        d = route(triage(p, render_dir=render))
        check("파서 없음", d.parser, None)
        check("처리 불가", d.routable, False)
        check_in("사유 이어 붙임", "처리 대상이 아님", d.reason)

        print("\n[8] 판정 근거가 JSON 으로 나간다 (on_route_decided Hook)")
        import json
        p = make_pdf(os.path.join(work, "10PCV071-DATA SHEET_REV1.pdf"), [SPEC])
        d = route(triage(p, render_dir=render))
        body = json.dumps(d.to_log(), ensure_ascii=False)
        check("직렬화 성공", "pymupdf" in body, True)
        check("보조 근거 표시 포함", "text_usable" in body, True)

        print("\n[9] 같은 입력 → 같은 출력 (철학 6)")
        tri = triage(p, render_dir=render)
        check("두 번 호출 동일", route(tri).to_log(), route(tri).to_log())

        print("\n[10] 임계값을 자체로 두지 않는다")
        from src.router.constants import TEXT_HINT_MIN_CHARS
        check("공용 preprocess.TEXT_LAYER_MIN 참조",
              TEXT_HINT_MIN_CHARS, preprocess.TEXT_LAYER_MIN)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print("\n" + "=" * 62)
    print(f"  통과 {ok} / 실패 {fail}")
    print("=" * 62)
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
