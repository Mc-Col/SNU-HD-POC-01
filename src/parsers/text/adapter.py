# -*- coding: utf-8 -*-
"""③-a TEXT PARSER 를 파이프라인에 꽂는 어댑터.

`src/pipeline.py` 는 `text_parser` 를 생성자로 주입받고 기본값이 `NullParser`
(모든 필드 N/A) 다. 이 어댑터를 넣으면 엑셀·텍스트 PDF 가 실제로 추출된다.

    from src.pipeline import Pipeline
    from src.parsers.text.adapter import TextParser
    Pipeline(text_parser=TextParser())

파이프라인 파일은 소유자만 고친다(CLAUDE.md 철학 1). 주입 지점이 이미 있으므로
여기서 계약만 맞춘다.
"""
from __future__ import annotations

import os
from typing import Sequence

from src.contracts import ParserType, RawExtraction, TriageResult

from .composite import CompositeIndex
from .excel import parse_excel
from .field_index import FieldIndex
from .pdf_text import ScannedPdfError, parse_pdf_text

EXCEL_EXT = {".xlsx", ".xlsm", ".xls"}


def spec_pages(triage: TriageResult) -> list[int] | None:
    """Triage 가 고른 사양표 페이지. 못 고르면 None(전체)."""
    sel = triage.selected_page
    if sel is not None:
        return [sel.page]
    pages = [p.page for p in triage.spec_pages]
    return pages or None


class TextParser:
    """계약 ②를 그대로 반환한다. 실패는 삼키지 않고 note 에 남긴다."""

    def __init__(self, index: FieldIndex | None = None,
                 composite: CompositeIndex | None = None) -> None:
        self.index = index or FieldIndex.load()
        self.composite = composite if composite is not None else CompositeIndex.load()

    def extract(self, path: str, triage: TriageResult,
                fields: Sequence) -> list[RawExtraction]:
        ext = os.path.splitext(path)[1].lower()
        pages = spec_pages(triage)
        ptype = ParserType.EXCEL if ext in EXCEL_EXT else ParserType.PDF_TEXT
        try:
            if ext in EXCEL_EXT:
                res = parse_excel(path, index=self.index, composite=self.composite,
                                  sheets=pages)
            elif ext == ".pdf":
                res = parse_pdf_text(path, index=self.index, composite=self.composite,
                                     pages=pages)
            else:
                return self._all_missing(fields, ptype, f"{ext} 는 텍스트 파서 담당이 아니다")
        except ScannedPdfError as e:
            # 조용히 0건을 돌려주지 않는다 — 왜 못 했는지가 남아야 한다 (원칙 5)
            return self._all_missing(fields, ptype, f"스캔 PDF — VLM 담당: {e}")

        got = res.by_key()
        out: list[RawExtraction] = []
        for f in fields:
            hit = got.get(f.key)
            out.append(hit if hit is not None else RawExtraction(
                field_key=f.key, raw_value=None, parser=ptype,
                note="문서에서 이 항목의 라벨을 찾지 못했다"))
        return out

    def reread(self, path: str, f, prev: RawExtraction,
               attempt: int = 1) -> RawExtraction | None:
        """텍스트 파서는 재판독이 없다 — 같은 글자를 다시 읽어도 같다."""
        return None

    @staticmethod
    def _all_missing(fields: Sequence, ptype: ParserType, note: str) -> list[RawExtraction]:
        return [RawExtraction(field_key=f.key, raw_value=None, parser=ptype, note=note)
                for f in fields]
