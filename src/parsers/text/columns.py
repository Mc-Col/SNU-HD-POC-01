# -*- coding: utf-8 -*-
"""공정 조건 Max / Nor / Min 열 인식 — 엑셀·PDF 파서가 함께 쓴다.

표준은 Normal 값을 쓴다 (2026-08-24 이종수 책임 확정 — Required Cv 는 Normal 값만).
열 머리글 표기가 늘어나면 schema/rules.yaml 로 옮긴다.
"""
from __future__ import annotations

COLUMN_HEADERS = {
    "MAX": "max", "MAXIMUM": "max",
    "NOR": "normal", "NORM": "normal", "NORMAL": "normal",
    "MIN": "min", "MINIMUM": "min",
    "DESIGN": "design", "DES": "design",
}
PREFERRED_COLUMN = "normal"
MIN_HEADERS = 2          # 이만큼 모여야 열 머리글 행으로 본다


def classify(text: object) -> str | None:
    return COLUMN_HEADERS.get(str(text or "").strip().upper().rstrip("."))


def anchor_from(cells: list[tuple[object, float | int]]) -> float | int | None:
    """(텍스트, 좌표) 목록이 열 머리글 행이면 Normal 열 좌표를 돌려준다."""
    hits: dict[str, float | int] = {}
    for text, pos in cells:
        kind = classify(text)
        if kind and kind not in hits:
            hits[kind] = pos
    if len(hits) < MIN_HEADERS:
        return None
    return hits.get(PREFERRED_COLUMN)
