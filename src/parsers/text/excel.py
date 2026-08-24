# -*- coding: utf-8 -*-
"""③-a TEXT PARSER (엑셀) — 헤더 텍스트를 표준 컬럼에 붙인다.

셀 좌표로 매핑하지 않는다. 벤더 양식은 디테일이 바뀌므로 헤더 텍스트가 기준이다.
값은 라벨의 오른쪽 → 아래 순서로 찾는다.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field

import openpyxl
from openpyxl.utils import get_column_letter

from src.contracts import ParserType, RawExtraction

from .field_index import FieldIndex

SCAN_RIGHT = 4          # 라벨 오른쪽으로 몇 칸까지 값을 찾는가
SCAN_DOWN = 2           # 오른쪽에 없으면 아래로 몇 칸까지
MAX_LABEL_LEN = 60      # 이보다 긴 문자열은 라벨이 아니라 본문으로 본다


@dataclass
class UnmappedLabel:
    """표준 컬럼에 붙지 못한 라벨. 유사표현 사전을 키우는 재료가 된다."""
    text: str
    source_locator: str
    neighbor_value: str


@dataclass
class TextParseResult:
    records: list[RawExtraction] = dc_field(default_factory=list)
    unmapped: list[UnmappedLabel] = dc_field(default_factory=list)

    def by_key(self) -> dict[str, RawExtraction]:
        return {r.field_key: r for r in self.records}


def _merged_index(ws) -> dict[tuple[int, int], tuple[tuple[int, int], object]]:
    """병합 셀의 모든 좌표 → (앵커 좌표, 병합범위)."""
    out: dict[tuple[int, int], tuple[tuple[int, int], object]] = {}
    for rng in ws.merged_cells.ranges:
        anchor = (rng.min_row, rng.min_col)
        for r in range(rng.min_row, rng.max_row + 1):
            for c in range(rng.min_col, rng.max_col + 1):
                out[(r, c)] = (anchor, rng)
    return out


def _text(v: object) -> str:
    return "" if v is None else str(v).strip()


def parse_excel(path: str, index: FieldIndex | None = None) -> TextParseResult:
    """엑셀 파일 하나 → RawExtraction[] + 미매핑 라벨."""
    ix = index or FieldIndex.load()
    wb = openpyxl.load_workbook(path, data_only=True)
    result = TextParseResult()
    seen: set[str] = set()                      # 먼저 찾은 값이 이긴다

    for page, ws in enumerate(wb.worksheets, start=1):
        merged = _merged_index(ws)
        consumed: set[tuple[int, int]] = set()   # 값으로 이미 쓰인 셀은 라벨이 아니다

        def cell_value(r: int, c: int) -> object:
            hit = merged.get((r, c))
            if hit is not None:
                ar, ac = hit[0]
                return ws.cell(ar, ac).value
            return ws.cell(r, c).value

        def locator(r: int, c: int) -> str:
            return f"{ws.title}!{get_column_letter(c)}{r}"

        for row in ws.iter_rows():
            for cell in row:
                r, c = cell.row, cell.column
                if (r, c) in consumed:
                    continue
                # 병합 영역이면 앵커에서만 처리한다 (같은 라벨 중복 방지)
                span = merged.get((r, c))
                if span is not None and span[0] != (r, c):
                    continue
                if not isinstance(cell.value, str):      # 숫자·날짜는 라벨이 아니다
                    continue

                label = _text(cell.value)
                if not label or len(label) > MAX_LABEL_LEN:
                    continue
                if not any(ch.isalpha() for ch in label):
                    continue

                hit = ix.lookup(label)
                value, vpos = _find_value(cell_value, ix, merged, r, c, span)
                if value:
                    consumed.add(vpos)

                if hit is None:
                    if value:
                        result.unmapped.append(
                            UnmappedLabel(label, locator(r, c), value)
                        )
                    continue

                if hit.key in seen:
                    continue
                seen.add(hit.key)

                found = bool(value)
                result.records.append(RawExtraction(
                    field_key=hit.key,
                    raw_value=value if found else None,
                    raw_label=label,
                    page=page,
                    confidence=hit.confidence if found else 0.0,
                    parser=ParserType.EXCEL,
                    source_locator=locator(*vpos) if found else locator(r, c),
                    note="" if found else "라벨은 찾았으나 값 셀이 비어 있음",
                ))

    result.records.sort(key=lambda x: x.field_key)
    result.unmapped.sort(key=lambda x: (x.source_locator, x.text))
    return result


def _find_value(cell_value, ix: FieldIndex, merged, r: int, c: int, span):
    """라벨 오른쪽 → 아래 순서로 값 셀을 찾는다. 다른 라벨을 만나면 멈춘다."""
    rng = span[1] if span else None
    right_from = rng.max_col if rng else c
    down_from = rng.max_row if rng else r

    for dc in range(1, SCAN_RIGHT + 1):
        pos = (r, right_from + dc)
        v = _text(cell_value(*pos))
        if not v:
            continue
        if ix.lookup(v) is not None:            # 값이 아니라 다음 라벨이다
            break
        return v, pos

    for dr in range(1, SCAN_DOWN + 1):
        pos = (down_from + dr, c)
        v = _text(cell_value(*pos))
        if not v:
            continue
        if ix.lookup(v) is not None:
            break
        return v, pos

    return "", (r, c)
