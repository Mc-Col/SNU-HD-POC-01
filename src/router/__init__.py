# -*- coding: utf-8 -*-
"""② ROUTER — 어느 파서로 보낼지

    from src.router import Router
    parser, reason, stats = Router().run(triage)     # → (ParserType, 근거, 통계)

## 이 모듈도 "조립" 이다

포맷 판별·페이지 탐침·렌더는 전부 `src/preprocess.py` 에 있고, 페이지별 판정
결과는 이미 `TriageResult.pages` 에 담겨 있다. Router 는 **그것을 보고 경로만
고른다.** 파일을 다시 열지 않는다.

## 이전 구현이 틀린 지점 (E2E 실측으로 확인)

텍스트 레이어가 있으면 텍스트 파서로 보냈다. 그 결과 값을 잃었다.

    10PCV071   텍스트 경로 → 9필드 전부 N/A  ·  VLM 경로 → 8/9 추출
    10FV634    텍스트 경로 → 5/9            ·  VLM 경로 → 9/9

`CLAUDE.md` 가 이유를 명시한다 — *"텍스트 레이어가 있다고 VLM 을 건너뛰지
않는다. 텍스트는 글자를 보증할 뿐이고 어느 값이 어느 필드인지는 VLM 이 정한다."*
실측 근거: 정비보고서 p2 는 텍스트가 온전한데 뽑아보면 `OPEN`/`CLOSE` 가 한
덩어리로 나오고 값이 라벨과 멀찍이 떨어져 나온다.

## 그래서 판정 규칙

    엑셀            → EXCEL. 셀 좌표가 남아 `source_locator` 가 정확하다
    그 외(PDF·tif)  → VLM. 텍스트 레이어가 있으면 **보조 근거로 함께 넘긴다**
                       (텍스트를 버리지 않는다 — 파서가 대조에 쓸 수 있다)

즉 이 Router 는 "텍스트냐 VLM 이냐" 를 가르지 않고 **"엑셀이냐 이미지냐"** 를
가른다. 텍스트 레이어 정보는 버리지 않고 통계로 실어 보내, 하류가 원하면
대조에 쓸 수 있게 한다.
"""
from __future__ import annotations                      # 타입 표기 일관성 유지

import logging                                          # 실패를 삼키지 않고 기록
import os                                               # 확장자 판별
from dataclasses import dataclass, field                # 처리 단위 타입
from typing import Any                                  # 통계 dict 값 타입

from src import preprocess                              # 조립할 공용 도구
from src.contracts import ParserType, TriageResult      # 계약 (복사하지 않고 import)

from .constants import (                                # 임계값·리더 이름
    READER_NONE,
    READER_OPENPYXL,
    READER_PYMUPDF,
    READER_XLRD,
    TEXT_HINT_MIN_CHARS,
)

logger = logging.getLogger(__name__)                    # 모듈 전용 로거

__all__ = ["Router", "route", "select_parser", "detect_format", "WorkUnit", "RouteDecision"]


# ══════════════════════════════════════════════════════════════════
#  출력 타입 — 계약에 Router 출력이 없으므로 모듈 내부에 정의한다
#             (ParserType 만 계약의 enum 을 쓴다)
# ══════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class WorkUnit:
    """파서가 처리할 단위 하나 (보통 페이지 한 장, 엑셀은 시트 하나)."""
    page: int                                            # 1부터
    render_path: str | None = None                        # 렌더된 PNG (VLM 입력)
    sheet: str | None = None                              # 엑셀 시트명
    has_text_layer: bool = False                          # 이 페이지에 텍스트가 있나
    text_len: int = 0                                     # 텍스트 길이 (보조 근거의 양)
    note: str = ""                                        # 관찰

    @property
    def text_usable(self) -> bool:
        """텍스트를 보조 근거로 쓸 만한가 (버리지 않되 과신하지 않는다)."""
        return self.has_text_layer and self.text_len >= TEXT_HINT_MIN_CHARS


@dataclass
class RouteDecision:
    """경로 판정 결과. `to_log()` 이 `on_route_decided` Hook 의 입력이 된다."""
    source_path: str
    parser: ParserType | None                             # None = 처리하지 않음
    reader: str                                           # 어느 리더로 열 것인가
    units: list[WorkUnit] = field(default_factory=list)   # 처리 단위
    reason: str = ""                                      # 판정 근거 (사람이 읽는다)
    evidence: dict[str, Any] = field(default_factory=dict)  # 판정에 쓴 수치

    @property
    def routable(self) -> bool:
        """파서로 보낼 수 있는 상태인가."""
        return self.parser is not None and bool(self.units)

    def to_log(self) -> dict[str, Any]:
        """JSON 직렬화 가능한 형태로 (Hook 로그용)."""
        return {
            "source_path": self.source_path,
            "parser": self.parser.value if self.parser else None,
            "reader": self.reader,
            "units": [
                {"page": u.page, "sheet": u.sheet, "render_path": u.render_path,
                 "has_text_layer": u.has_text_layer, "text_len": u.text_len,
                 "text_usable": u.text_usable, "note": u.note}
                for u in self.units
            ],
            "reason": self.reason,
            "evidence": self.evidence,
        }


# ══════════════════════════════════════════════════════════════════
#  포맷 판별 — 리더 선택까지 한 번에
# ══════════════════════════════════════════════════════════════════

def detect_format(path: str) -> tuple[str, str]:
    """확장자로 (계열, 리더) 를 정한다.

    역할  : 포맷별 리더 분기. `xls` 는 openpyxl 로 읽을 수 없어 xlrd 가 필요하다.
    입력  : path — 파일 경로
    출력  : (계열, 리더 이름). 계열은 "excel" / "pdf" / "image" / "" (미지원)
    부수효과: 없음 (파일을 열지 않는다)

    확장자 목록을 자체로 두지 않고 공용 상수를 쓴다 — 지원 범위가 한 곳에서만
    바뀌어야 한다.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in preprocess.EXCEL_EXT:
        # xlsx·xlsm 은 openpyxl, xls(BIFF) 는 xlrd. 섞으면 읽기 실패한다.
        reader = READER_XLRD if ext == ".xls" else READER_OPENPYXL
        return "excel", reader
    if ext in preprocess.PDF_EXT:
        return "pdf", READER_PYMUPDF
    if ext in preprocess.IMAGE_EXT:
        return "image", READER_PYMUPDF
    return "", READER_NONE


# ══════════════════════════════════════════════════════════════════
#  진입점
# ══════════════════════════════════════════════════════════════════

class Router:
    """`RouterModule` 구현 — 파이프라인이 `run(triage)` 로 호출한다."""

    def run(self, triage: TriageResult) -> tuple[ParserType, str, dict[str, Any]]:
        """하네스 규약에 맞춰 (파서, 근거, 통계) 를 돌려준다.

        입력  : triage — ① Triage 결과
        출력  : (ParserType, 근거 문자열, 통계 dict)
        부수효과: 없음
        """
        decision = route(triage)
        stats = dict(decision.evidence)
        stats["reader"] = decision.reader
        stats["units"] = len(decision.units)
        # 파서가 없으면(처리 불가) 하네스가 기대하는 형태를 지키기 위해 VLM 으로 표시하되
        # 사유를 남긴다 — 조용히 EXCEL 로 보내면 엉뚱한 파서가 빈 결과를 낸다.
        return (decision.parser or ParserType.VLM), decision.reason, stats


def route(triage: TriageResult) -> RouteDecision:
    """② Router 본체 — Triage 가 판정한 페이지를 보고 경로를 고른다.

    입력  : triage — ① Triage 결과 (pages · targets · selected_page 를 쓴다)
    출력  : RouteDecision
    부수효과: 없음. **파일을 다시 열지 않는다** — Triage 가 이미 탐침·렌더했다.
    """
    path = triage.source_path
    family, reader = detect_format(path)

    evidence: dict[str, Any] = {
        "family": family,
        "page_count": triage.stats.get("page_count"),
        "text_pages": triage.stats.get("text_pages"),
        "mixed_text_scan": triage.stats.get("mixed_text_scan"),
        "rendered_pages": triage.stats.get("rendered_pages"),
        "document_class": triage.document_class.value,
    }

    # ── 처리 대상이 아니면 여기서 끝 ──────────────────────────────
    if not triage.processable:
        return RouteDecision(
            source_path=path, parser=None, reader=READER_NONE,
            reason=f"처리 대상이 아님 ({triage.document_class.value}) — {triage.reason[:120]}",
            evidence=evidence,
        )

    if not family:                                       # 미지원 포맷 (Triage 가 걸렀어야 한다)
        return RouteDecision(
            source_path=path, parser=None, reader=READER_NONE,
            reason=f"미지원 포맷 {os.path.splitext(path)[1]}",
            evidence=evidence,
        )

    # ── 처리할 페이지를 고른다 ────────────────────────────────────
    #   Triage 가 사양표를 골랐으면 그 페이지만, 못 골랐으면 전 페이지.
    #   전 페이지를 보내는 것은 낭비지만, 임의로 한 장을 고르면 근거 없는 선택이 된다.
    target_pages = [t.page_from for t in (triage.targets or []) if t.page_from]
    by_page = {p.page: p for p in (triage.pages or [])}
    if target_pages:
        chosen = [by_page[n] for n in target_pages if n in by_page]
        unit_source = "triage_selected"
    else:
        chosen = list(triage.pages or [])
        unit_source = "all_pages"
    evidence["unit_source"] = unit_source

    # Triage 가 후보를 못 가려 **일부러** 고르지 않은 경우를 근거에 드러낸다.
    #   판정 재료가 없어서 페이지가 비어 있는 것과, 사람이 골라야 해서 비워 둔
    #   것은 다른 상태다. 이 구분이 로그에 남지 않으면 처리 실패와 섞인다.
    spec_candidates = [p.page for p in (triage.pages or []) if p.is_spec]
    declined = not target_pages and len(spec_candidates) > 1
    if declined:
        evidence["triage_declined_selection"] = spec_candidates

    # ── 엑셀 — 셀 좌표가 남으므로 텍스트 파서로 ────────────────────
    if family == "excel":
        units = [
            WorkUnit(page=p.page, sheet=p.kind_hint or None,
                     has_text_layer=p.has_text_layer, text_len=p.text_len,
                     render_path=p.render_path,
                     note="엑셀 시트 — 셀 좌표를 source_locator 로 남길 수 있다")
            for p in chosen
        ] or [WorkUnit(page=1, note="시트 정보 없음 — 전체를 읽는다")]
        return RouteDecision(
            source_path=path, parser=ParserType.EXCEL, reader=reader, units=units,
            reason=(f"엑셀 계열 — {reader} 로 읽는다 "
                    f"(시트 {len(units)}개, 대상={unit_source})"
                    + (f" | Triage 가 사양표 후보 p{spec_candidates} 중 최신본을 "
                       f"가리지 못해 시트를 고르지 않았다 — 사람이 선택해야 한다"
                       if declined else "")),
            evidence=evidence,
        )

    # ── PDF · 이미지 — VLM 으로 보낸다 ────────────────────────────
    #   텍스트 레이어가 있어도 VLM 을 건너뛰지 않는다. 텍스트는 글자를 보증할
    #   뿐이고 어느 값이 어느 필드인지는 VLM 이 정한다(CLAUDE.md).
    #   다만 텍스트를 **버리지 않는다** — 보조 근거로 실어 보낸다.
    units = [
        WorkUnit(page=p.page, render_path=p.render_path,
                 has_text_layer=p.has_text_layer, text_len=p.text_len,
                 note=("텍스트 보조 근거 있음" if p.has_text_layer and
                       p.text_len >= TEXT_HINT_MIN_CHARS else "스캔 — 이미지 판독"))
        for p in chosen
    ]
    missing_render = [u.page for u in units if not u.render_path]
    if missing_render:                                   # 렌더가 없으면 VLM 이 볼 것이 없다
        evidence["missing_render_pages"] = missing_render

    usable = sum(1 for u in units if u.text_usable)      # 텍스트를 보조로 쓸 수 있는 페이지 수
    evidence["text_hint_pages"] = usable
    reason = (f"{family} — VLM 경로 (페이지 {len(units)}개, 대상={unit_source}, "
              f"텍스트 보조 {usable}개). 텍스트 레이어가 있어도 건너뛰지 않는다")
    if declined:
        reason += (f" | Triage 가 사양표 후보 p{spec_candidates} 중 최신본을 가리지 "
                   f"못해 페이지를 고르지 않았다 — 사람이 선택해야 한다")
    if missing_render:
        reason += f" | 렌더 없는 페이지 {missing_render} — 판독 불가로 남는다"

    return RouteDecision(
        source_path=path, parser=ParserType.VLM, reader=reader, units=units,
        reason=reason, evidence=evidence,
    )


def select_parser(triage: TriageResult, path: str | None = None) -> RouteDecision:
    """설계도가 명시한 이름의 진입점 (`route` 와 같다).

    입력  : triage — Triage 결과, path — 무시(계약의 source_path 를 쓴다)
    출력  : RouteDecision
    부수효과: 없음
    """
    return route(triage)
