# -*- coding: utf-8 -*-
"""
전처리 유틸리티 — 파일명 파싱 · 페이지 렌더링 · 텍스트 레이어 탐침

강민호 책임의 src/triage · src/router 가 이 함수들을 조립해서 쓴다.
여기에는 판정 로직을 넣지 않는다 — 사실을 수집하는 도구만 둔다.

    from src import preprocess as pre

    info  = pre.parse_filename(path)        # 태그 · 문서종류 · rev
    pages = pre.probe_pages(path)           # 페이지별 텍스트 레이어 유무
    pngs  = pre.render_pages(path, out_dir) # 페이지 → PNG
    grid  = pre.make_montage(pngs, out)     # 격자 1장 (VLM 이진 판정용)

실측 근거 (raw_file 1,089건, 2026-08-24):
    파일명에 태그+문서종류 둘 다  95.5%   ← 파일 단위 판정이 거의 무료
    PDF 페이지 중 텍스트 보유     68.1%
    PDF 파일 중 텍스트/스캔 혼재  57.4%   ← 그래서 페이지 단위로 판정해야 한다
    다중 페이지 파일              85%     (최대 61페이지)
"""
from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable

# 페이지당 텍스트가 이 글자 수 이상이면 텍스트 레이어가 있는 것으로 본다.
# 실측 결과 20~500자 사이에서 판정이 거의 변하지 않는다 — 텍스트 페이지는
# 중앙값 2,055자이고 스캔은 0자대여서 그 사이가 비어 있다. 튜닝 불필요.
TEXT_LAYER_MIN = 100

EXCEL_EXT = {".xlsx", ".xlsm", ".xls"}
IMAGE_EXT = {".tif", ".tiff", ".jpg", ".jpeg", ".png"}
PDF_EXT = {".pdf"}
SUPPORTED = EXCEL_EXT | IMAGE_EXT | PDF_EXT

# 손글씨 스캔의 판독을 위해 기본 해상도를 넉넉히 둔다.
# 원본이 150 DPI 1비트라 그대로 쓰면 손글씨가 뭉개진다.
RENDER_DPI = 200


# ══════════════════════════════════════════════════════════════
#  파일명 파싱
# ══════════════════════════════════════════════════════════════

# 문서 종류 — 오타와 뒤 공백이 실제로 존재한다(REPIAR REPORT, "REPAIR REPORT ").
# 느슨하게 잡되 긴 패턴을 먼저 검사한다.
DOC_KINDS: list[tuple[str, str, bool]] = [
    # (표시명, 정규식, 대상 여부)
    ("SPECIFICATION DATA SHEET", r"SPEC\w*\s*DATA\s*SHEET", True),
    ("DATA SHEET",               r"DATA\s*SHEET",           True),
    ("REPAIR REPORT",            r"REP[AI]{2}R\s*REPORT",   False),
    ("RETROFIT REPORT",          r"RETROFIT\s*REPORT",      False),
    ("RETROFIT",                 r"RETROFIT",               False),
    ("TEST REPORT",              r"TEST\s*REPORT",          False),
    ("DRAWING",                  r"DRAWING",                False),
    ("SPECIFICATION",            r"SPECIFICATION",          True),
]

# 태그 — 파일명은 10FV012, 문서는 10-FV-012 처럼 쓰인다.
TAG_RE = re.compile(r"(\d{1,3})\s*-?\s*([A-Z]{1,4})\s*-?\s*(\d{2,4})\s*-?\s*([A-Z]{0,2})")
REV_RE = re.compile(r"_?REV\.?\s*(\d+)", re.I)


@dataclass
class FileNameInfo:
    path: str
    stem: str
    ext: str
    tag: str | None = None          # 정규화된 태그 (10FV012)
    tag_raw: str | None = None      # 파일명에 있던 그대로
    doc_kind: str = ""              # 표시명
    in_scope: bool | None = None    # True 대상 / False 제외 / None 판단 불가
    rev: str = ""

    @property
    def supported_ext(self) -> bool:
        return self.ext in SUPPORTED


def normalize_tag(s: str | None) -> str | None:
    """10-FV-012 · 10FV012 · 10 FV 012 를 같은 값으로 만든다.

    파일명 태그와 문서 안 태그를 비교하려면 이 정규화가 필수다.
    """
    if not s:
        return None
    m = TAG_RE.search(unicodedata.normalize("NFKC", str(s)).upper())
    if not m:
        return None
    unit, kind, num, suf = m.groups()
    return f"{int(unit)}{kind}{num}{suf}"


def find_tags(text: str) -> list[str]:
    """텍스트에서 태그를 모두 뽑는다. 순서 유지, 중복 제거."""
    out, seen = [], set()
    for m in TAG_RE.finditer(unicodedata.normalize("NFKC", str(text)).upper()):
        unit, kind, num, suf = m.groups()
        if not kind or len(num) < 2:
            continue
        t = f"{int(unit)}{kind}{num}{suf}"
        if t not in seen:
            seen.add(t); out.append(t)
    return out


def parse_filename(path: str) -> FileNameInfo:
    """파일명에서 태그·문서종류·rev 를 뽑는다. 전체의 95.5% 가 여기서 해결된다."""
    base = os.path.basename(path)
    stem, ext = os.path.splitext(base)
    info = FileNameInfo(path=path, stem=stem, ext=ext.lower())

    up = unicodedata.normalize("NFKC", stem).upper()

    for name, pat, scope in DOC_KINDS:
        if re.search(pat, up):
            info.doc_kind, info.in_scope = name, scope
            break

    # 태그는 파일명 앞부분에서 찾는다 (10FV012-DATA SHEET_REV1)
    head = re.split(r"[-_]", stem, maxsplit=1)[0]
    info.tag = normalize_tag(head) or normalize_tag(stem)
    if info.tag:
        m = TAG_RE.search(unicodedata.normalize("NFKC", stem).upper())
        info.tag_raw = m.group(0).strip() if m else None

    m = REV_RE.search(stem)
    if m:
        info.rev = f"REV{m.group(1)}"
    return info


# ══════════════════════════════════════════════════════════════
#  페이지 탐침 — 페이지별 텍스트 레이어 유무
# ══════════════════════════════════════════════════════════════

@dataclass
class PageText:
    page: int                       # 1부터
    text: str = ""
    has_text_layer: bool = False
    width: int = 0
    height: int = 0
    locator: str = ""               # 엑셀 시트명 등

    @property
    def text_len(self) -> int:
        return len(self.text.strip())


def probe_pages(path: str) -> list[PageText]:
    """페이지별로 텍스트 레이어가 있는지 본다.

    PDF 파일의 57.4% 가 텍스트·스캔 혼재이므로 파일 단위로 판정하면 안 된다.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in PDF_EXT:
        return _probe_pdf(path)
    if ext in IMAGE_EXT:
        return _probe_image(path)
    if ext in EXCEL_EXT:
        return _probe_excel(path)
    return []


def _probe_pdf(path: str) -> list[PageText]:
    import pymupdf
    pymupdf.TOOLS.mupdf_display_errors(False)
    out = []
    with pymupdf.open(path) as d:
        for i, pg in enumerate(d, start=1):
            t = pg.get_text() or ""
            out.append(PageText(
                page=i, text=t,
                has_text_layer=len(t.strip()) >= TEXT_LAYER_MIN,
                width=int(pg.rect.width), height=int(pg.rect.height)))
    return out


def _probe_image(path: str) -> list[PageText]:
    """이미지는 텍스트 레이어가 없다. 다중 페이지 TIF 를 놓치지 않는 것이 요점."""
    from PIL import Image
    out = []
    with Image.open(path) as im:
        n = getattr(im, "n_frames", 1)
        for i in range(n):
            im.seek(i)
            out.append(PageText(page=i + 1, has_text_layer=False,
                                width=im.size[0], height=im.size[1]))
    return out


def _probe_excel(path: str) -> list[PageText]:
    """엑셀은 셀 값이 곧 텍스트 레이어다. PDF 보다 정확하다 — 주소가 정확하니까."""
    ext = os.path.splitext(path)[1].lower()
    out = []
    if ext == ".xls":
        import xlrd
        book = xlrd.open_workbook(path)
        for i, sh in enumerate(book.sheets(), start=1):
            buf = []
            for r in range(sh.nrows):
                for c in range(sh.ncols):
                    v = sh.cell_value(r, c)
                    if v not in ("", None):
                        buf.append(str(v))
            txt = "\n".join(buf)
            out.append(PageText(page=i, text=txt,
                                has_text_layer=len(txt.strip()) >= TEXT_LAYER_MIN,
                                locator=sh.name))
    else:
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        for i, ws in enumerate(wb.worksheets, start=1):
            buf = []
            for row in ws.iter_rows(values_only=True):
                for v in row:
                    if v is not None and str(v).strip():
                        buf.append(str(v))
            txt = "\n".join(buf)
            out.append(PageText(page=i, text=txt,
                                has_text_layer=len(txt.strip()) >= TEXT_LAYER_MIN,
                                locator=ws.title))
        wb.close()
    return out


# ══════════════════════════════════════════════════════════════
#  페이지 렌더링 — 모든 포맷을 PNG 로
# ══════════════════════════════════════════════════════════════
#
#  JPG 가 아니라 PNG 를 쓴다. JPG 는 손실 압축이라 글자 주변에 잡티가 생기고,
#  1비트 150DPI 손글씨 스캔에서는 그 잡티가 판독을 방해한다.

def render_pages(path: str, out_dir: str, dpi: int = RENDER_DPI,
                 pages: Iterable[int] | None = None) -> list[str]:
    """페이지를 PNG 로 렌더링하고 경로 목록을 돌려준다.

    pages 를 주면 그 페이지만 렌더한다 (1부터). 사양표 한 장만 필요할 때
    나머지를 렌더하지 않아 시간과 디스크를 아낀다.
    """
    os.makedirs(out_dir, exist_ok=True)
    ext = os.path.splitext(path)[1].lower()
    want = set(pages) if pages else None

    if ext in PDF_EXT:
        return _render_pdf(path, out_dir, dpi, want)
    if ext in IMAGE_EXT:
        return _render_image(path, out_dir, want)
    if ext in EXCEL_EXT:
        return _render_excel(path, out_dir, dpi, want)
    raise ValueError(f"렌더링을 지원하지 않는 포맷: {ext}")


def _stem(path: str) -> str:
    s = os.path.splitext(os.path.basename(path))[0]
    return re.sub(r"[^\w.-]+", "_", s)[:70]


def _render_pdf(path, out_dir, dpi, want) -> list[str]:
    import pymupdf
    pymupdf.TOOLS.mupdf_display_errors(False)
    out = []
    with pymupdf.open(path) as d:
        for i, pg in enumerate(d, start=1):
            if want and i not in want:
                continue
            p = os.path.join(out_dir, f"{_stem(path)}_p{i:02d}.png")
            pg.get_pixmap(dpi=dpi).save(p)
            out.append(p)
    return out


def _render_image(path, out_dir, want) -> list[str]:
    """다중 페이지 TIF 를 전부 뽑는다. 1비트는 그레이스케일로 올려 판독을 돕는다."""
    from PIL import Image
    out = []
    with Image.open(path) as im:
        n = getattr(im, "n_frames", 1)
        for i in range(n):
            if want and (i + 1) not in want:
                continue
            im.seek(i)
            fr = im.convert("L") if im.mode == "1" else im.convert("RGB")
            p = os.path.join(out_dir, f"{_stem(path)}_p{i + 1:02d}.png")
            fr.save(p, "PNG")
            out.append(p)
    return out


def _render_excel(path, out_dir, dpi, want) -> list[str]:
    """엑셀 → PDF → PNG. 시트 1개 = PNG 1장으로 맞춘다.

    통째로 내보내면 5시트가 인쇄면 13장이 되어 probe_pages()의 시트 번호와
    어긋난다(has_text_layer 는 시트 기준, render_path 는 인쇄면 기준).
    엑셀의 의미 단위는 인쇄면이 아니라 시트이므로 시트마다 따로 내보낸다.

    실패하면 예외를 던지지 않고 빈 목록을 돌려준다(Triage 가 판정하게).
    """
    try:
        import win32com.client as win32
        import pymupdf
        pymupdf.TOOLS.mupdf_display_errors(False)
    except ImportError:
        return []

    out, tmps = [], []
    excel = None
    try:
        excel = win32.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        wb = excel.Workbooks.Open(os.path.abspath(path), ReadOnly=True)
        try:
            for idx in range(1, wb.Worksheets.Count + 1):
                if want and idx not in want:
                    continue
                sh = wb.Worksheets(idx)
                ps = sh.PageSetup
                ps.Zoom = False
                ps.FitToPagesWide = 1      # 한 장에 다 담아 시트=페이지를 보장
                ps.FitToPagesTall = 1
                ps.Orientation = 2         # 가로
                ps.PrintArea = ""          # 저장된 인쇄영역이 표를 자르는 것을 막는다
                tmp = os.path.join(out_dir, f"{_stem(path)}_s{idx:02d}.pdf")
                sh.ExportAsFixedFormat(0, os.path.abspath(tmp))
                if not os.path.exists(tmp):
                    continue
                tmps.append(tmp)
                png = os.path.join(out_dir, f"{_stem(path)}_p{idx:02d}.png")
                with pymupdf.open(tmp) as d:
                    if d.page_count:
                        d[0].get_pixmap(dpi=dpi).save(png)
                        out.append(png)
        finally:
            wb.Close(False)
    except Exception:
        return []
    finally:
        if excel is not None:
            try:
                excel.Quit()
            except Exception:
                pass
        for t in tmps:
            try:
                os.remove(t)
            except OSError:
                pass
    return out


# ══════════════════════════════════════════════════════════════
#  격자 합성 — VLM 이진 판정을 한 번에
# ══════════════════════════════════════════════════════════════

def make_montage(png_paths: list[str], out_path: str, cols: int = 3,
                 cell: int = 480, label: bool = True,
                 max_cells: int = 12) -> list[str]:
    """썸네일을 격자로 합쳐 판정용 시트를 만든다. 시트 경로 목록을 돌려준다.

    8페이지를 VLM 8회가 아니라 1회로 판정하기 위한 것. 실제 8페이지 파일로
    확인한 결과 480px 썸네일에서 사양표·도면·부품리스트·표지·계산서가
    구분된다. 단 태그 글자는 이 크기에서 읽히지 않는다 — 종류 판정까지만
    쓰고, 태그 대조는 원본 해상도에서 따로 한다.

    max_cells 로 나눠 담는 이유: Claude 는 긴 변 1568px 를 넘으면 이미지를
    줄인다. 61페이지를 한 장에 담으면 셀이 224px 로 줄어 판독이 불가능해진다.
    12칸(3x4=1440px)이 그 한계 안에 들어가는 최대치다.
    """
    from PIL import Image, ImageDraw
    n = len(png_paths)
    if n == 0:
        raise ValueError("렌더된 페이지가 없습니다")

    root, ext = os.path.splitext(out_path)
    ext = ext or ".png"
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    sheets = []
    batches = [png_paths[i:i + max_cells] for i in range(0, n, max_cells)]
    for bi, batch in enumerate(batches):
        c = max(1, min(cols, len(batch)))
        rows = (len(batch) + c - 1) // c
        sheet = Image.new("RGB", (c * cell, rows * cell), (255, 255, 255))
        draw = ImageDraw.Draw(sheet)
        for i, p in enumerate(batch):
            with Image.open(p) as im:
                t = im.convert("RGB")
                t.thumbnail((cell - 10, cell - 26))
                x, y = (i % c) * cell + 5, (i // c) * cell + 22
                sheet.paste(t, (x, y))
            if label:
                # 칸 번호는 파일 전체 기준 페이지 번호 — VLM 이 번호로 답한다
                pno = bi * max_cells + i + 1
                bx, by = (i % c) * cell + 5, (i // c) * cell + 3
                draw.rectangle([bx, by, bx + 54, by + 17], fill=(31, 78, 107))
                draw.text((bx + 6, by + 4), f"p{pno}", fill=(255, 255, 255))
            draw.rectangle([(i % c) * cell + 2, (i // c) * cell + 2,
                            (i % c + 1) * cell - 2, (i // c + 1) * cell - 2],
                           outline=(200, 200, 200))
        out = f"{root}{ext}" if len(batches) == 1 else f"{root}_{bi + 1}{ext}"
        sheet.save(out, "PNG")
        sheets.append(out)
    return sheets


# ══════════════════════════════════════════════════════════════
#  자체 확인
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys, glob, tempfile
    sys.stdout.reconfigure(encoding="utf-8")

    print("=" * 72)
    print("  전처리 유틸리티 자체 확인")
    print("=" * 72)

    print("\n[1] 태그 정규화 — 파일명과 문서 표기가 같은 값이 되는가")
    for a, b in [("10FV012", "10-FV-012"), ("10FV007B", "10-FV-007B"),
                 ("10PV002", "10-PV-002"), ("10FV002", "10 FV 002")]:
        na, nb = normalize_tag(a), normalize_tag(b)
        ok = "OK  " if na == nb and na else "실패"
        print(f"  {ok} {a:<12} vs {b:<14} → {na} / {nb}")

    print("\n[2] 파일명 파싱 — 실제 코퍼스")
    names = sorted({os.path.normcase(p) for p in glob.glob("raw_file/*")})
    if names:
        stat = {"대상": 0, "제외": 0, "판단불가": 0, "태그없음": 0}
        for p in names:
            i = parse_filename(p)
            stat["대상" if i.in_scope else "제외" if i.in_scope is False
                 else "판단불가"] += 1
            if not i.tag:
                stat["태그없음"] += 1
        tot = len(names)
        for k, v in stat.items():
            print(f"  {k:<10}{v:>5}건  {v/tot*100:>5.1f}%")
        print("\n  예시:")
        for p in names[:3] + [p for p in names if "10fv012" in p.lower()][:1]:
            i = parse_filename(p)
            print(f"    {os.path.basename(p)[:44]:<46} tag={i.tag} "
                  f"kind={i.doc_kind or '-'} scope={i.in_scope} {i.rev}")

    print("\n[3] 페이지 탐침 + 렌더링 — 다중 페이지 TIF")
    target = next((p for p in names if "10fv012" in p.lower()
                   and p.lower().endswith(".tif")), None)
    if target:
        pt = probe_pages(target)
        print(f"  {os.path.basename(target)} — {len(pt)}페이지")
        for x in pt[:3]:
            print(f"    p{x.page}: {x.width}x{x.height} 텍스트레이어={x.has_text_layer} "
                  f"{x.text_len}자")
        tmp = os.path.join(tempfile.gettempdir(), "d2s_render_check")
        pngs = render_pages(target, tmp, pages=[1, 4])
        print(f"  선택 렌더 (p1, p4): {len(pngs)}장")
        for p in pngs:
            print(f"    {os.path.basename(p)}  {os.path.getsize(p):,} bytes")
        allp = render_pages(target, tmp)
        grid = make_montage(allp, os.path.join(tmp, "montage.png"))
        print(f"  격자 합성: {len(allp)}페이지 → {len(grid)}장 "
              f"({', '.join(os.path.basename(g) for g in grid)})")

    print("\n[4] 엑셀 렌더링 실현성 — Excel COM")
    xl = next((p for p in names if p.lower().endswith(".xlsx")
               and "DATA SHEET" in p.upper()), None)
    if xl:
        tmp = os.path.join(tempfile.gettempdir(), "d2s_render_check")
        pngs = render_pages(xl, tmp)
        if pngs:
            print(f"  OK   {os.path.basename(xl)[:40]} → {len(pngs)}장")
            for p in pngs[:3]:
                from PIL import Image
                with Image.open(p) as im:
                    print(f"       {os.path.basename(p)}  {im.size}  "
                          f"{os.path.getsize(p):,} bytes")
        else:
            print(f"  실패 {os.path.basename(xl)[:40]} — Excel COM 사용 불가")
            print("       → 엑셀 경로는 텍스트 파싱만으로 가야 함 (화면에 원본 표시 불가)")
    else:
        print("  대상 엑셀 없음")
    print()
