# -*- coding: utf-8 -*-
"""
합성 데이터시트 페이지 생성기 — 회사 문서를 화면 검증에 쓰지 않기 위한 스탠드인.

    python fixtures/ui/make_sample.py

sample_document_result.json 의 bbox 위치에 raw_value 를 그린다.
지면과 bbox 가 구조적으로 일치하므로 좌표를 손으로 맞출 일이 없고,
픽스처의 값을 고치면 지면도 따라 바뀐다.

같은 입력 → 같은 출력 (난수·시각 없음).
"""
from __future__ import annotations

import io
import json
import os

import pymupdf

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "sample_document_result.json")
OUT_PDF = os.path.join(HERE, "sample_page.pdf")
OUT_MULTI = os.path.join(HERE, "sample_multipage.pdf")
OUT_TIF = os.path.join(HERE, "sample_scan.tif")

INK = (0.09, 0.13, 0.17)
RULE = (0.72, 0.75, 0.72)
BAND = (0.90, 0.92, 0.90)

# 섹션 띠 — 이 지면의 겉모습은 이 스크립트가 소유한다
SECTIONS = [
    (88, "GENERAL"),
    (242, "PROCESS DATA"),
    (374, "VALVE BODY / TRIM"),
    (490, "ACTUATOR"),
    (558, "VALVE PERFORMANCE"),
]


def _abs(bbox, w: float, h: float) -> tuple[float, float, float, float]:
    """정규화 bbox(0~1) → 페이지 좌표. 계약이 정규화이므로 여기서 되돌린다."""
    x0, y0, x1, y1 = bbox
    return (x0 * w, y0 * h, x1 * w, y1 * h)


def _row(page: pymupdf.Page, label: str, text: str, bbox) -> None:
    x0, y0, x1, y1 = _abs(bbox, page.rect.width, page.rect.height)
    base = y1 - 4.5
    page.insert_text((58, base), label, fontname="helv", fontsize=7.5, color=INK)
    page.insert_text((x0 + 4, base), text, fontname="hebo", fontsize=8.5, color=INK)
    page.draw_line((52, y1 + 2), (543, y1 + 2), color=RULE, width=0.4)


def _load() -> dict:
    with io.open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def spec_page(doc, fx: dict):
    """픽스처의 bbox 자리에 값을 그린 사양표 한 장. 다중 페이지에서도 이걸 쓴다."""
    w, h = fx["page_size"]
    page = doc.new_page(width=w, height=h)

    # 외곽 · 제목
    page.draw_rect(pymupdf.Rect(46, 40, 549, 622), color=RULE, width=0.7)
    page.insert_text((58, 60), "CONTROL VALVE SPECIFICATION SHEET",
                     fontname="hebo", fontsize=11, color=INK)
    page.insert_text((58, 74), "PROJECT  D2S PoC   ·   SYNTHETIC SAMPLE — NOT A REAL DOCUMENT",
                     fontname="helv", fontsize=7, color=INK)
    page.insert_text((452, 60), "SHEET 1 OF 1", fontname="helv", fontsize=7.5, color=INK)
    page.insert_text((452, 74), "REV. A   2003-05-26", fontname="helv",
                     fontsize=7.5, color=INK)

    # 섹션 띠
    for y, title in SECTIONS:
        page.draw_rect(pymupdf.Rect(52, y - 11, 543, y + 3), color=None, fill=BAND)
        page.insert_text((58, y), title, fontname="hebo", fontsize=8, color=INK)

    # 픽스처가 근거로 지목한 위치에 값을 그린다
    drawn = 0
    for fd in fx["fields"]:
        if not fd.get("bbox"):
            continue                      # 지면에 없는 필드 → 화면에서 N/A 로 남는다
        _row(page, str(fd.get("raw_label") or ""), str(fd.get("raw_value") or ""), fd["bbox"])
        drawn += 1

    # 추출 대상이 아닌 행 — 실제 문서는 우리가 뽑는 것보다 항목이 많다
    for it in fx.get("furniture", []):
        _row(page, it["label"], it["text"], it["bbox"])

    page.insert_text((58, 614), "NOTE  SYNTHETIC FIXTURE FOR UI VERIFICATION.",
                     fontname="helv", fontsize=7, color=INK)

    return page, drawn


def build() -> str:
    fx = _load()
    doc = pymupdf.open()
    _, drawn = spec_page(doc, fx)
    doc.set_metadata({})                  # 시각 정보 제거 → 바이트 재현성
    doc.save(OUT_PDF, deflate=True)
    doc.close()
    return f"{OUT_PDF}  (필드 {drawn}행 + 대상 외 {len(fx.get('furniture', []))}행)"




# ══════════════════════════════════════════════════════════════
#  다중 페이지 — 쪽 고르기 화면 검증용
# ══════════════════════════════════════════════════════════════
#
#  실물 `10FV011` 의 구조를 합성으로 재현한다. 사양표가 2장이고 하나는
#  1986년 폐기본인데, **폐기 표시가 손글씨라 텍스트 레이어에 없다.**
#  그래서 축소 이미지로는 가릴 수 없고 크게 봐야 한다 — 그 상황이
#  화면의 비교 뷰가 존재하는 이유이고, 픽스처가 그것을 재현해야 한다.

def _decoy(doc, fx, title: str, rows: list[tuple[str, str]], note: str):
    w, h = fx["page_size"]
    page = doc.new_page(width=w, height=h)
    page.draw_rect(pymupdf.Rect(46, 40, 549, 622), color=RULE, width=0.7)
    page.insert_text((58, 60), title, fontname="hebo", fontsize=11, color=INK)
    page.insert_text((58, 74), note, fontname="helv", fontsize=7, color=INK)
    y = 110
    for a, b in rows:
        page.insert_text((58, y), a, fontname="helv", fontsize=7.5, color=INK)
        page.insert_text((254, y), b, fontname="hebo", fontsize=8.5, color=INK)
        page.draw_line((52, y + 4), (543, y + 4), color=RULE, width=0.4)
        y += 22
    return page


def _hand_old(page, x: float, y: float, scale: float = 1.0) -> None:
    """손으로 쓴 "OLD" — **벡터로 그린다.**

    글자로 넣으면 텍스트 레이어에 남아 `find_marks` 가 공짜로 잡아버린다.
    실물은 스캔 위의 손글씨라 아무 도구도 읽지 못하고 사람만 본다.
    픽스처가 그 조건을 재현해야 화면의 비교 뷰가 진짜로 시험된다.
    """
    red, wdt = (0.78, 0.12, 0.12), 2.6 * scale
    s = 26 * scale
    page.draw_oval(pymupdf.Rect(x, y, x + s * 0.8, y + s), color=red, width=wdt)
    lx = x + s * 1.05
    page.draw_line((lx, y), (lx, y + s), color=red, width=wdt)
    page.draw_line((lx, y + s), (lx + s * 0.55, y + s), color=red, width=wdt)
    dx = x + s * 1.85
    page.draw_line((dx, y), (dx, y + s), color=red, width=wdt)
    page.draw_bezier((dx, y), (dx + s * 0.95, y + s * 0.12),
                     (dx + s * 0.95, y + s * 0.88), (dx, y + s),
                     color=red, width=wdt)


def build_multipage() -> str:
    fx = _load()
    doc = pymupdf.open()

    spec_page(doc, fx)                                   # p1 · 2003 개조본
    _decoy(doc, fx, "PIPING ARRANGEMENT DRAWING",
           [("DWG NO.", "D-2-1041 SH.3"), ("SCALE", "1:50"),
            ("REV", "2")], "SYNTHETIC SAMPLE - NOT A REAL DOCUMENT")   # p2
    _decoy(doc, fx, "PARTS LIST",
           [("ITEM 01", "BODY ASSY"), ("ITEM 02", "TRIM SET"),
            ("ITEM 03", "GASKET KIT")], "SYNTHETIC SAMPLE")            # p3

    old = _decoy(doc, fx, "CONTROL VALVE SPECIFICATION SHEET",
                 [("TAG NO.", "10-FV-001"), ("MANUFACTURER", "FISHER"),
                  ("MODEL", "657-ED"), ("BODY SIZE", '2"'),
                  ("RATED CV", "70.7"), ("FAIL POSITION", "AIR TO OPEN (ATO)")],
                 "DATE SEP 6 1986   ORIGINAL ISSUE")                   # p4
    _hand_old(old, 400, 96, 1.6)

    doc.set_metadata({})
    doc.save(OUT_MULTI, deflate=True)
    n = doc.page_count
    doc.close()
    return f"{OUT_MULTI}  ({n}쪽 · 사양표 2장 · 폐기 표시는 손글씨라 텍스트 없음)"

def build_tif() -> str:
    """다중 페이지 PDF 를 **1비트 스캔 tif** 로 바꾼다.

    대상의 71.9% 가 스캔 tif 이므로 화면의 주 경로가 여기다. 실물의 조건을
    그대로 만든다 — 텍스트 레이어가 없고(날짜·개정표기 배지가 안 붙는다),
    1비트라 `_render_image` 의 그레이스케일 승격 분기를 지나간다.

    이 픽스처에서는 규칙이 최신성을 판정할 수 없다(`판정불가`). 그것이
    스캔의 정직한 결과이고, 그래서 사람이 지면을 보고 고른다.
    """
    from PIL import Image
    if not os.path.exists(OUT_MULTI):
        build_multipage()
    with pymupdf.open(OUT_MULTI) as doc:
        frames = []
        for pg in doc:
            pix = pg.get_pixmap(matrix=pymupdf.Matrix(2.08, 2.08))   # 약 150DPI
            im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            frames.append(im.convert("1"))                            # 1비트 스캔
    frames[0].save(OUT_TIF, save_all=True, append_images=frames[1:],
                   compression="group4")
    return f"{OUT_TIF}  ({len(frames)}쪽 · 1비트 · 텍스트 레이어 없음)"


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print("생성:", build())
    print("생성:", build_multipage())
    print("생성:", build_tif())
