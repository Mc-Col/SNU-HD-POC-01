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

INK = (0.09, 0.13, 0.17)
RULE = (0.72, 0.75, 0.72)
BAND = (0.90, 0.92, 0.90)

# 섹션 띠 — 이 지면의 겉모습은 이 스크립트가 소유한다
SECTIONS = [
    (88, "GENERAL"),
    (242, "PROCESS DATA"),
    (374, "VALVE BODY / TRIM"),
    (462, "ACTUATOR"),
    (536, "VALVE PERFORMANCE"),
]


def _row(page: pymupdf.Page, label: str, text: str, bbox) -> None:
    x0, y0, x1, y1 = bbox
    base = y1 - 4.5
    page.insert_text((58, base), label, fontname="helv", fontsize=7.5, color=INK)
    page.insert_text((x0 + 4, base), text, fontname="hebo", fontsize=8.5, color=INK)
    page.draw_line((52, y1 + 2), (543, y1 + 2), color=RULE, width=0.4)


def build() -> str:
    with io.open(FIXTURE, encoding="utf-8") as f:
        fx = json.load(f)

    w, h = fx["page_size"]
    doc = pymupdf.open()
    page = doc.new_page(width=w, height=h)

    # 외곽 · 제목
    page.draw_rect(pymupdf.Rect(46, 40, 549, 600), color=RULE, width=0.7)
    page.insert_text((58, 60), "CONTROL VALVE SPECIFICATION SHEET",
                     fontname="hebo", fontsize=11, color=INK)
    page.insert_text((58, 74), "PROJECT  D2S PoC   ·   SYNTHETIC SAMPLE — NOT A REAL DOCUMENT",
                     fontname="helv", fontsize=7, color=INK)
    page.insert_text((452, 60), "SHEET 1 OF 1", fontname="helv", fontsize=7.5, color=INK)
    page.insert_text((452, 74), "REV. A", fontname="helv", fontsize=7.5, color=INK)

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

    page.insert_text((58, 592), "NOTE  SYNTHETIC FIXTURE FOR UI VERIFICATION.",
                     fontname="helv", fontsize=7, color=INK)

    doc.set_metadata({})                  # 시각 정보 제거 → 바이트 재현성
    doc.save(OUT_PDF, deflate=True)
    doc.close()
    return f"{OUT_PDF}  (필드 {drawn}행 + 대상 외 {len(fx.get('furniture', []))}행)"


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print("생성:", build())
