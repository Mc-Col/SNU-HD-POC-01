# -*- coding: utf-8 -*-
"""bbox 오버레이 — 이 화면이 없으면 검증이 성립하지 않는다.

`FieldRecord.bbox` 가 근거다. 초록 실선은 "여기서 읽었고 확신함",
노란 점선은 "여기 같은데 확신 없음". 선택한 필드는 굵게 + 음영.

화면정의서의 '마우스 오버'는 Streamlit 네이티브에 hover 이벤트가 없어
'행 선택'으로 바꿨다. 시연에서는 클릭 한 번 차이고, 결정론적이며 캐시된다.
"""
from __future__ import annotations

import pymupdf
import streamlit as st

from src.contracts import FieldRecord, FieldState
from src.ui.theme import AMBER, GREEN


def _rgb(hex_: str) -> tuple[float, float, float]:
    h = hex_.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def boxes_for(records: list[FieldRecord], page: int, selected: str | None) -> tuple:
    """캐시 키가 되도록 원시 튜플만 담는다."""
    out = []
    for r in records:
        if not r.bbox or r.page != page:
            continue
        out.append((
            float(r.bbox[0]), float(r.bbox[1]), float(r.bbox[2]), float(r.bbox[3]),
            r.state.value,
            (r.raw_label or r.field_name)[:26],
            r.field_key == selected,
        ))
    return tuple(out)


IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}


def _open_drawable(path: str):
    """그릴 수 있는 문서로 연다. 이미지는 PDF 로 감싼다.

    `pymupdf.open(png)` 은 열리지만 `draw_rect` 가 "is no PDF" 로 실패한다.
    tif 가 대상의 71.9% 이므로 이 변환 없이는 화면에 아무것도 못 띄운다.
    """
    import os
    if os.path.splitext(path)[1].lower() in IMAGE_EXT:
        img = pymupdf.open(path)
        pdf_bytes = img.convert_to_pdf()
        img.close()
        return pymupdf.open("pdf", pdf_bytes)
    return pymupdf.open(path)


def _abs_rect(x0, y0, x1, y1, page) -> "pymupdf.Rect":
    """bbox 를 페이지 좌표로. 계약은 정규화 0.0~1.0 이다.

    1 을 넘는 값이 오면 이미 절대 좌표로 보고 그대로 쓴다 — 픽스처가
    절대 좌표로 적혀 있어 둘 다 받아야 한다. 섞여 들어와도 각각 옳게 그린다.
    """
    w, h = page.rect.width, page.rect.height
    if max(x0, y0, x1, y1) <= 1.0:
        return pymupdf.Rect(x0 * w, y0 * h, x1 * w, y1 * h)
    return pymupdf.Rect(x0, y0, x1, y1)


@st.cache_data(show_spinner=False)
def render(pdf_path: str, page_no: int, boxes: tuple, zoom: float = 2.0) -> bytes:
    """같은 (지면, 박스, 선택) → 같은 PNG. 재실행마다 다시 그리지 않는다."""
    doc = _open_drawable(pdf_path)
    page = doc[max(0, page_no - 1)]

    for x0, y0, x1, y1, state, tag, is_sel in boxes:
        if state == FieldState.NA.value:
            continue                                  # 지면에 근거가 없으니 그릴 곳도 없다
        review = state == FieldState.REVIEW.value
        color = _rgb(AMBER if review else GREEN)
        rect = _abs_rect(x0, y0, x1, y1, page)
        page.draw_rect(
            rect, color=color, width=2.2 if is_sel else 1.1,
            dashes="[3 3] 0" if review else None,
            fill=color, fill_opacity=0.22 if is_sel else 0.09,
        )
        # 항목명 꼬리표 — 문서에 적혀 있던 라벨(raw_label) 을 그대로 쓴다.
        # 박스 오른쪽에 붙인다. 위에 붙이면 행 간격이 좁은 양식에서 윗 박스를 가린다.
        tw = 3.9 * len(tag) + 8
        cy = (rect.y0 + rect.y1) / 2
        lx = min(rect.x1 + 3, page.rect.width - tw - 2)      # 지면 밖으로 안 나가게
        page.draw_rect(pymupdf.Rect(lx, cy - 4.5, lx + tw, cy + 4.5),
                       color=None, fill=color, fill_opacity=1.0)
        page.insert_text((lx + 3.5, cy + 2.2), tag, fontname="helv",
                         fontsize=6, color=(1, 1, 1))

    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
    png = pix.tobytes("png")
    doc.close()
    return png


def page_count(pdf_path: str) -> int:
    doc = _open_drawable(pdf_path)
    n = doc.page_count
    doc.close()
    return n
