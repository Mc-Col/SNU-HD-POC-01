# -*- coding: utf-8 -*-
"""③-a TEXT PARSER (텍스트 PDF) — 헤더 텍스트를 표준 컬럼에 붙인다.

실물 사양서에서 관찰된 배치 두 가지를 처리한다.
  ① 한 덩어리 안에 "Label : Value"        (머리글 블록)
  ② 같은 행에 라벨 · 값이 x 좌표로 분리    (좌우 2단 사양표)

엑셀 파서와 같은 FieldIndex 를 쓴다. 유사표현은 코드가 아니라 스키마에만 있다.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

import fitz

from src.contracts import ParserType, RawExtraction

from src.preprocess import probe_pages

from .columns import anchor_from
from .composite import CompositeIndex, try_split
from .excel import MAX_LABEL_LEN, TextParseResult, UnmappedLabel
from .field_index import FieldIndex
from .units import UnitIndex

Y_TOL = 3.0                 # 이 이내면 같은 행으로 본다
MAX_VALUE_CELLS = 6         # 한 행에서 값 후보를 몇 개까지 볼 것인가
COLUMN_TOL = 12.0           # 열 머리글과 값의 x 오차 허용
                            # 실측(52PV014): 블록 안의 값은 머리글 ±6 안에 선다.
                            # 24 로 두면 2단 양식 오른쪽 라벨 열(21 떨어짐)까지 삼킨다.


class ScannedPdfError(RuntimeError):
    """텍스트 레이어가 없는 PDF. 이 파서가 아니라 VLM 이 처리해야 한다."""



_COLON = re.compile(r"\s*[:：]\s*")


@dataclass(frozen=True)
class _Cell:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str


def _has_alnum(t: str) -> bool:
    return any(ch.isalnum() for ch in t)


def _rows(page) -> list[list[_Cell]]:
    """페이지의 텍스트 조각을 행 단위로 묶는다. y → x 순서로 결정적으로 정렬."""
    cells: list[_Cell] = []
    for block in page.get_text("dict")["blocks"]:
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                t = span["text"].strip()
                if t:
                    x0, y0, x1, y1 = span["bbox"]
                    cells.append(_Cell(x0, y0, x1, y1, t))

    cells.sort(key=lambda c: (round(c.y0, 1), c.x0))
    rows: list[list[_Cell]] = []
    for c in cells:
        if rows and abs(c.y0 - rows[-1][0].y0) <= Y_TOL:
            rows[-1].append(c)
        else:
            rows.append([c])
    for r in rows:
        r.sort(key=lambda c: c.x0)
    return rows


def _column_anchor(row: list[_Cell]) -> float | None:
    """Max/Nor/Min 열 머리글 행이면 Normal 열의 x 좌표를 돌려준다."""
    return anchor_from([(c.text, c.x0) for c in row])


def parse_pdf_text(
    path: str,
    index: FieldIndex | None = None,
    pages: list[int] | None = None,
    composite: CompositeIndex | None = None,
    units: UnitIndex | None = None,
) -> TextParseResult:
    """텍스트 레이어가 있는 PDF → RawExtraction[] + 미매핑 라벨.

    pages 는 1-based. None 이면 전체. Triage 가 사양표 페이지를 지정하면 그것만 본다.
    """
    ix = index or FieldIndex.load()
    cix = composite if composite is not None else CompositeIndex.load()
    uix = units if units is not None else UnitIndex.load()
    doc = fitz.open(path)
    result = TextParseResult()
    seen: set[str] = set()

    targets = list(pages or range(1, doc.page_count + 1))

    # 실패를 삼키지 않는다 — 스캔본을 조용히 0건으로 돌려주면 원인을 알 수 없다.
    # 판정은 preprocess 가 한다 (CLAUDE.md — 전처리를 다시 만들지 않는다).
    # 문서 단위로 본다: 특정 페이지만 요청했다고 멀쩡한 문서를 스캔본으로 몰면 안 된다.
    probed = {p.page: p for p in probe_pages(path)}
    if probed and not any(p.has_text_layer for p in probed.values()):
        chars = sum(p.text_len for p in probed.values())
        raise ScannedPdfError(
            f"텍스트 레이어가 거의 없음 ({chars}자) — 스캔 PDF 로 보인다. "
            f"③-b VLM 파서가 처리해야 한다: {os.path.basename(path)}")

    # 페이지 단위로 걸러내지는 않는다. `has_text_layer` 는 "VLM 이 필요한가" 의
    # 기준(100자)이라, 글자가 적어도 라벨 몇 개는 읽히는 페이지를 버리게 된다.
    for pno in targets:
        page = doc[pno - 1]
        anchor: float | None = None         # 현재 유효한 Normal 열 x 좌표
        for ri, row in enumerate(_rows(page), start=1):
            found_anchor = _column_anchor(row)
            if found_anchor is not None:
                anchor = found_anchor
                continue                    # 머리글 행 자체는 값이 없다
            consumed: set[int] = set()

            for ci, cell in enumerate(row):
                if ci in consumed:
                    continue

                label, value, vcell, vidx = _split(cell, row, ci, ix, consumed, anchor, uix)
                if label is None:
                    continue

                loc = f"p{pno}:L{ri}:c{(vidx if value else ci) + 1}"

                split = try_split(cix, ix, label, value)
                if split is not None:
                    ok, pending = split
                    for pc in ok:
                        if pc.field_key in seen:
                            continue
                        seen.add(pc.field_key)
                        result.records.append(RawExtraction(
                            field_key=pc.field_key,
                            raw_value=pc.value,
                            raw_label=pc.label,
                            bbox=(vcell.x0, vcell.y0, vcell.x1, vcell.y1),
                            page=pno,
                            confidence=pc.confidence,
                            parser=ParserType.PDF_TEXT,
                            source_locator=loc,
                            note=f"복합 라벨 '{label}' 분해",
                        ))
                    for pc in pending:
                        result.unmapped.append(UnmappedLabel(pc.label, loc, pc.value))
                    continue

                hit = ix.lookup(label)
                if hit is None:
                    if value:
                        result.unmapped.append(UnmappedLabel(
                            label, f"p{pno}:L{ri}:c{ci + 1}", value))
                    continue
                if hit.key in seen:
                    continue
                seen.add(hit.key)

                found = bool(value)
                if not found:
                    note = "라벨은 찾았으나 값이 비어 있음"
                elif anchor is not None and abs(vcell.x0 - anchor) <= COLUMN_TOL:
                    note = "Max/Nor/Min 중 Normal 열 선택"
                else:
                    note = ""
                result.records.append(RawExtraction(
                    field_key=hit.key,
                    raw_value=value if found else None,
                    raw_label=label,
                    bbox=(vcell.x0, vcell.y0, vcell.x1, vcell.y1) if found else
                         (cell.x0, cell.y0, cell.x1, cell.y1),
                    page=pno,
                    confidence=hit.confidence if found else 0.0,
                    parser=ParserType.PDF_TEXT,
                    source_locator=f"p{pno}:L{ri}:c{(vidx if found else ci) + 1}",
                    note=note,
                ))

    result.records.sort(key=lambda x: x.field_key)
    result.unmapped.sort(key=lambda x: (x.source_locator, x.text))
    return result


def _split(cell: _Cell, row: list[_Cell], ci: int, ix: FieldIndex,
           consumed: set[int], anchor: float | None = None,
           units: UnitIndex | None = None):
    """셀 하나에서 (라벨, 값, 값셀, 값인덱스) 를 뽑는다."""
    # ① "Label : Value" — 한 덩어리 안에 콜론
    if _COLON.search(cell.text):
        parts = _COLON.split(cell.text, maxsplit=1)
        left = parts[0].strip()
        right = parts[1].strip() if len(parts) > 1 else ""
        if left and len(left) <= MAX_LABEL_LEN and any(c.isalpha() for c in left):
            if right and _has_alnum(right):
                return left, right, cell, ci
            # 콜론 뒤가 비었으면 오른쪽 셀에서 찾는다
            v, vc, vi = _right_value(row, ci, ix, consumed, anchor, units)
            return left, v, vc or cell, vi if vc else ci
        return None, "", cell, ci

    # ② 라벨 단독 → 같은 행 오른쪽에서 값
    t = cell.text
    if not t or len(t) > MAX_LABEL_LEN or not any(c.isalpha() for c in t):
        return None, "", cell, ci
    v, vc, vi = _right_value(row, ci, ix, consumed, anchor, units)
    return t, v, vc or cell, vi if vc else ci


_NUMERIC = re.compile(r"^[\d.,/%\s+-]+$")


def _is_label_with_value(row: list[_Cell], j: int, units: UnitIndex | None) -> bool:
    """Normal 열 자리의 후보가 값이 아니라 라벨인가.

    2단 양식에서는 오른쪽 단의 라벨 열이 Normal 열 근처를 지나간다
    (실물 `52PV014`: `Body Color | GRAY`, `Air Connection | 1/4" NPT`).
    숫자는 라벨이 아니므로 Max/Nor/Min 이 나란히 선 진짜 블록은 영향받지 않는다.
    """
    t = row[j].text.strip()
    if _NUMERIC.match(t):
        return False
    for k in range(j + 1, min(len(row), j + 3)):
        s = row[k].text.strip()
        if s and _has_alnum(s) and not (units is not None and units.is_unit(s)):
            return True
    return False


def _right_value(row: list[_Cell], ci: int, ix: FieldIndex, consumed: set[int],
                 anchor: float | None = None, units: UnitIndex | None = None):
    """같은 행 오른쪽 값. Normal 열을 알면 그 열을 우선한다."""
    cands: list[tuple[int, _Cell]] = []
    for j in range(ci + 1, min(len(row), ci + 1 + MAX_VALUE_CELLS)):
        t = row[j].text.strip()
        if not t or not _has_alnum(t):
            continue
        if units is not None and units.is_unit(t):   # 단위 칸은 건너뛴다
            continue
        if ix.lookup(t) is not None:            # 값이 아니라 다음 라벨
            break
        cands.append((j, row[j]))
    if not cands:
        return "", None, ci

    if anchor is not None:
        near = [(j, c) for j, c in cands
                if abs(c.x0 - anchor) <= COLUMN_TOL
                and not _is_label_with_value(row, j, units)]
        if near:
            j, c = near[0]
            consumed.add(j)
            return c.text.strip(), c, j

    j, c = cands[0]
    consumed.add(j)
    return c.text.strip(), c, j
