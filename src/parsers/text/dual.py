# -*- coding: utf-8 -*-
"""③ 두 경로 파서 — VLM 이 정하고 텍스트가 보증한다.

■ 무슨 작업인가
────────────────────────────────────────────────────────────────────
한 문서를 두 방법으로 읽고, **다르면 사람에게 넘긴다.**

    ③-b VLM 파서    이미지를 보고 어느 값이 어느 필드인지 정한다
    ③-a 텍스트 파서  글자 층에서 라벨과 값을 읽는다

CLAUDE.md 가 둘의 역할을 갈라 놓았다.

    "텍스트 레이어가 있다고 VLM 을 건너뛰지 않는다.
     텍스트는 **글자를 보증할 뿐**이고 어느 값이 어느 필드인지는 VLM 이 정한다."

대조 자체는 `crosscheck.py` 가 한다. 이 파일은 그것을 **파이프라인 계약에 끼우는
껍데기**다.

■ 왜 파이프라인을 고치지 않고 래퍼로 만들었나
────────────────────────────────────────────────────────────────────
`src/pipeline.py` 의 `run_document()` 는 Router 가 고른 파서 **하나만** 부른다.

    parser = self.vlm_parser if ptype is ParserType.VLM else self.text_parser
    extractions = parser.extract(path, tri, fields)

여기에 "두 파서를 돌린다" 를 넣으려면 소유자(이종수 책임) 파일을 고쳐야 한다.
그런데 `ParserModule` 계약(`extract` · `reread`)만 지키면 **파이프라인은 자기가
두 파서를 부르는지 알 필요가 없다.** 조립 단계에서 감싸면 끝이다.

    Pipeline(vlm_parser=DualParser(vlm=VlmParser(), text=TextParser()))

계약 밖을 만지지 않는다(철학 1)를 지키면서 같은 결과를 얻는 방법이다.

■ 불일치를 어떻게 사람에게 넘기나
────────────────────────────────────────────────────────────────────
새 상태를 만들지 않는다. 파이프라인의 `_decide()` 가 이미 이렇게 판정한다.

    if ex.confidence < f.threshold:  → FieldState.REVIEW ("확신도 임계 미달")

그래서 **불일치 필드의 confidence 를 0 으로 내리고 note 에 두 값을 적는다.**
상태 판정 로직도, 계약도 건드릴 필요가 없다.

일치했을 때 confidence 를 **올리지는 않는다.** 확신도는 모델이 낸 값이고, 여기서
임의로 올리면 자동확정 문턱이 실제보다 낮아진다. 낮은 확신도를 승격하는 일은
평가 Agent(⑤ 뒤)의 몫으로 이미 설계돼 있다.
"""
from __future__ import annotations

import os
from dataclasses import replace
from typing import Any, Sequence

from src.contracts import RawExtraction

from . import crosscheck as cc
from .adapter import TextParser

# 텍스트 파서가 다룰 수 있는 확장자. 그 밖이면 VLM 결과만 쓴다.
TEXT_EXT = {".xlsx", ".xlsm", ".xls", ".pdf"}


class DualParser:
    """VLM 과 텍스트 파서를 함께 돌리고 대조한다. `ParserModule` 계약 구현."""

    def __init__(self, vlm: Any, text: Any | None = None, *,
                 fill_from_text: bool = True) -> None:
        """
        vlm  — ③-b VLM 파서 (`ParserModule`). 필드 배정의 주인이다.
        text — ③-a 텍스트 파서. 기본값은 `TextParser()`.
        fill_from_text
            VLM 이 아무 값도 못 낸 필드를 텍스트 후보로 채울지.
            **채우더라도 confidence 0 이라 자동확정되지 않는다** — 사람이 보고
            확정한다. 빈칸을 그대로 두는 것보다 후보를 보여주는 편이 검토가 빠르다.
            VLM 이 낸 값을 텍스트로 덮는 일은 **하지 않는다**(필드 배정은 VLM 몫).
        """
        self.vlm = vlm
        self.text = text if text is not None else TextParser()
        self.fill_from_text = fill_from_text
        # 마지막 실행의 대조 결과 — 로그·발표 숫자로 쓴다
        self.last_agreements: list[cc.Agreement] = []
        self.last_summary: dict[str, int] = {}

    # ── 계약 ──────────────────────────────────────────────────
    def extract(self, path: str, triage: Any,
                fields: Sequence[Any]) -> list[RawExtraction]:
        keys = [f.key for f in fields]
        vlm_recs = {r.field_key: r for r in (self.vlm.extract(path, triage, fields) or [])}
        text_recs = {r.field_key: r for r in self._text_extract(path, triage, fields)}

        agreements = cc.crosscheck(list(vlm_recs.values()), list(text_recs.values()),
                                   fields=keys)
        self.last_agreements = agreements
        self.last_summary = cc.summary(agreements)

        out: list[RawExtraction] = []
        for a in agreements:
            out.append(self._merge(a, vlm_recs.get(a.field_key), text_recs.get(a.field_key)))
        return out

    def reread(self, path: str, f: Any, prev: RawExtraction,
               attempt: int = 1) -> RawExtraction | None:
        """Loop A 재판독은 VLM 만 한다.

        텍스트 파서는 같은 글자를 다시 읽어도 같은 값이 나오므로 재시도가 의미 없다.
        """
        rr = getattr(self.vlm, "reread", None)
        return rr(path, f, prev, attempt) if callable(rr) else None

    # ── 내부 ──────────────────────────────────────────────────
    def _text_extract(self, path: str, triage: Any,
                      fields: Sequence[Any]) -> list[RawExtraction]:
        """텍스트 파서 실행. **여기서 실패해도 VLM 경로를 막지 않는다.**

        보증하는 쪽이 넘어졌다고 판독 자체를 포기할 이유가 없다. 다만 조용히
        넘기지도 않는다 — 사유를 note 에 남겨 로그에 드러난다(철학 5).
        """
        if os.path.splitext(path)[1].lower() not in TEXT_EXT:
            return []
        try:
            return list(self.text.extract(path, triage, fields) or [])
        except Exception as e:                       # noqa: BLE001 - 어떤 실패든 VLM 은 계속한다
            return [RawExtraction(field_key=f.key, raw_value=None,
                                  note=f"텍스트 파서 실패: {type(e).__name__} {e}")
                    for f in fields]

    def _merge(self, a: cc.Agreement, vlm: RawExtraction | None,
               text: RawExtraction | None) -> RawExtraction:
        """대조 결과를 계약 ②(RawExtraction) 하나로 합친다."""
        base = vlm or RawExtraction(field_key=a.field_key, raw_value=None)

        notes = [base.note, a.as_note()]
        # 텍스트 쪽이 값을 못 냈고 사유를 남겼으면 함께 싣는다 — 왜 대조하지
        # 못했는지가 로그에 남아야 한다 (철학 5).
        if text is not None and not str(text.raw_value or "").strip() and text.note:
            notes.append(text.note)

        if a.state == cc.CONFLICT:
            # 두 경로가 다르다 → 확신도를 0 으로 내려 REVIEW 로 보낸다.
            # 값은 VLM 것을 남긴다. 텍스트 값은 note 에 적어 화면에서 나란히 보인다.
            return replace(base, confidence=0.0,
                           note=_join(*notes, "사람 확인 필요"))

        if a.state == cc.TEXT_ONLY and self.fill_from_text and text is not None:
            # VLM 이 놓친 것을 후보로 채운다. confidence 0 이므로 자동확정되지 않는다.
            # 근거 좌표는 텍스트 쪽 것을 쓴다 — 사람이 그 자리를 볼 수 있어야 한다.
            return replace(base, raw_value=text.raw_value, raw_label=text.raw_label,
                           source_locator=text.source_locator, page=text.page,
                           confidence=0.0,
                           note=_join(*notes, "VLM 미추출 — 사람 확인 필요"))

        # 일치 · 표기차이 · VLM만 · 둘 다 없음 — 값과 확신도는 VLM 것을 그대로 두고
        # 무슨 상태였는지만 남긴다. (표기차이는 같은 값이므로 사람을 부르지 않는다)
        return replace(base, note=_join(*notes))


def _join(*parts: str) -> str:
    return " | ".join(str(p).strip() for p in parts if p and str(p).strip())
