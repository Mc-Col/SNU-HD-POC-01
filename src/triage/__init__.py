# -*- coding: utf-8 -*-
"""① TRIAGE — 이 파일에 데이터가 있나

    from src.triage import Triage
    result = Triage().run(path)          # → TriageResult

## 이 모듈은 "조립" 이다

도구는 전부 `src/preprocess.py` 에 있다 — 파일명 판정 · 페이지 텍스트 탐침 ·
페이지 렌더 · 최신성 선택. 이 모듈은 그것을 **순서대로 엮어 판정만 한다.**
다시 만들지 않는다(CLAUDE.md).

이전 구현은 태그 정규식 · 문서종류 키워드 · 텍스트 탐침을 자체 구현했고,
그 결과 두 가지가 어긋났다.

    ① 정비·개조 보고서를 파일명 키워드로 배제했다. 공용 `DOC_KINDS` 는
       `REPAIR REPORT` · `RETROFIT REPORT` · `TEST REPORT` 를 **대상(True)** 으로
       두고 있다. 실측 110건이 잘못 배제되었고 그 안에 골든 d006 이 있었다.
    ② 스캔 문서(코퍼스의 71.9%)는 텍스트가 없어 페이지 선정 자체가 실행되지
       않았다. 텍스트 기반으로 태그를 찾는 구조여서 스캔에서는 무효였다.

## 판정 순서

    1. 확장자         미지원 → UNSUPPORTED (예외를 던지지 않는다)
    2. 파일명         `scope_reason()` 이 사유를 주면 → OUT_OF_SCOPE
    3. 페이지 탐침    `probe_pages()` — 페이지별 텍스트 레이어·길이
    4. 페이지 렌더    `render_pages()` — `render_path` 를 채운다.
                      화면이 스캔 71.9% 를 표시하는 유일한 경로다(설계도 1순위)
    5. 페이지 판정    텍스트가 있으면 텍스트로 사양표 여부·태그·날짜를 읽고,
                      없으면 `PageClass.OTHER` + `render_path` 로 두어 VLM 이 판정한다
    6. 최신성 선택    `pick_latest_spec()` — 태그 단독성이 아니라 최신성이다
    7. 주의 문구      `caution_reason()` — 정비 보고서는 "사양 칸이 비어 있을 수 있음"

## 하지 말 것

    · 예외를 던지지 말 것 — 미지원·읽기 실패는 기록하고 정상 반환한다
    · 태그 단독성으로 페이지를 고르지 말 것 — 폐기본을 고른다(10FV011)
    · 파일 간 판단을 하지 말 것 — 범위 밖이다(2026-08-24 결정)
"""
from __future__ import annotations                      # 타입 표기 일관성 유지

import logging                                          # 실패를 삼키지 않고 기록
import os                                               # 확장자·경로
import tempfile                                         # 렌더 기본 위치

from src import preprocess                              # 조립할 공용 도구
from src.contracts import (                             # 계약 (복사하지 않고 import)
    DocumentClass,
    PageClass,
    PageInfo,
    Target,
    TriageResult,
)

from .constants import (                                # 임계값·확신도
    CONFIDENCE_AMBIGUOUS_SPEC,
    CONFIDENCE_FILENAME_ONLY,
    CONFIDENCE_NO_SPEC,
    CONFIDENCE_OUT_OF_SCOPE,
    CONFIDENCE_SPEC_SELECTED,
    CONFIDENCE_UNSUPPORTED,
    MIN_TEXT_FOR_SPEC_JUDGEMENT,
    RENDER_SUBDIR,
    SPEC_HEADER_KEYWORDS,
)

logger = logging.getLogger(__name__)                    # 모듈 전용 로거

__all__ = ["Triage", "triage", "classify_pages", "probe_structure", "match_filename_pattern"]


# ══════════════════════════════════════════════════════════════════
#  ① 파일명 판정 — 공용 parse_filename 을 그대로 쓴다
# ══════════════════════════════════════════════════════════════════

def match_filename_pattern(path: str):
    """파일명에서 태그·문서종류·rev 를 얻는다.

    역할  : 공용 `preprocess.parse_filename` 을 호출할 뿐이다. 이름을 유지하는
            이유는 설계도가 이 함수명으로 호출을 명시했기 때문이다.
    입력  : path — 파일 경로
    출력  : `preprocess.FileNameInfo`
    부수효과: 없음 (파일을 열지 않는다)
    """
    return preprocess.parse_filename(path)              # 자체 정규식을 두지 않는다


# ══════════════════════════════════════════════════════════════════
#  ② 구조 통계 — 페이지 탐침 결과를 stats 로 요약
# ══════════════════════════════════════════════════════════════════

def probe_structure(path: str) -> tuple[list, dict]:
    """페이지별 텍스트 레이어를 탐침하고 구조 통계를 만든다.

    역할  : 공용 `probe_pages` 를 호출하고 그 결과를 `TriageResult.stats` 형태로 요약한다.
    입력  : path — 파일 경로
    출력  : (PageText 목록, stats dict)
    부수효과: 파일을 읽기 전용으로 연다. 실패해도 예외를 올리지 않고 stats 에 사유를 남긴다.
    """
    try:
        pages = preprocess.probe_pages(path)            # 공용 탐침 (포맷별 분기까지 내부 처리)
    except Exception as exc:                            # 실패를 삼키지 않는다 — 기록하고 빈 목록
        logger.warning("페이지 탐침 실패 %s: %s", path, exc)
        return [], {"probe_error": f"{type(exc).__name__}: {exc}"[:200], "page_count": 0}

    text_pages = [p for p in pages if p.has_text_layer]  # 텍스트 레이어가 있는 페이지
    stats = {
        "ext": os.path.splitext(path)[1].lower(),
        "page_count": len(pages),
        "text_pages": len(text_pages),
        "text_len_total": sum(p.text_len for p in pages),
        "text_len_max": max((p.text_len for p in pages), default=0),
        # 혼재 여부 — PDF 의 57.4% 가 텍스트·스캔 혼재라 파일 단위 판정이 위험하다
        "mixed_text_scan": 0 < len(text_pages) < len(pages),
        "locators": [p.locator for p in pages if p.locator][:20],   # 엑셀 시트명 등
    }
    return pages, stats


# ══════════════════════════════════════════════════════════════════
#  ③ 페이지 판정 — 텍스트가 있으면 텍스트로, 없으면 VLM 에 넘긴다
# ══════════════════════════════════════════════════════════════════

def _looks_like_spec(text: str) -> bool:
    """텍스트에 사양표 머리글이 있는지 본다.

    입력  : text — 페이지 텍스트
    출력  : 사양표로 보이면 True
    부수효과: 없음

    텍스트가 짧으면 판정하지 않는다 — 도장·표제만 텍스트인 스캔본이 있어서,
    적은 글자로 판정하면 사양표를 놓치거나 아닌 것을 사양표로 만든다.
    """
    if len(text.strip()) < MIN_TEXT_FOR_SPEC_JUDGEMENT:
        return False
    upper = text.upper()
    return any(keyword in upper for keyword in SPEC_HEADER_KEYWORDS)


def classify_pages(path: str, pages, render_dir: str | None = None) -> list[PageInfo]:
    """페이지별 `PageInfo` 를 만든다 — 렌더 경로·태그·날짜·사양표 여부를 채운다.

    역할  : Router·VLM·화면이 모두 이 목록을 소비한다. `render_path` 는 화면이
            스캔 문서를 표시하는 유일한 경로다.
    입력  : path — 파일 경로, pages — `probe_structure` 가 돌려준 PageText 목록,
            render_dir — 렌더 결과를 둘 위치(None 이면 임시 폴더)
    출력  : PageInfo 목록 (1페이지부터 순서대로)
    부수효과: 페이지를 PNG 로 렌더해 파일을 쓴다. 렌더 실패는 기록하고 계속한다.
    """
    out_dir = render_dir or os.path.join(tempfile.gettempdir(), RENDER_SUBDIR)

    rendered: dict[int, str] = {}                       # 페이지 번호 → PNG 경로
    try:
        paths = preprocess.render_pages(path, out_dir)  # 전 페이지 렌더 (화면이 쓴다)
        for page_text, png in zip(pages, paths):        # 탐침 순서와 렌더 순서가 같다
            rendered[page_text.page] = png
    except Exception as exc:                            # 렌더 실패로 판정 전체를 막지 않는다
        logger.warning("페이지 렌더 실패 %s: %s", path, exc)

    infos: list[PageInfo] = []
    for page_text in pages:
        text = page_text.text or ""
        is_spec = _looks_like_spec(text)                 # 텍스트로 사양표 여부를 본다
        marker, superseded = preprocess.find_marks(text) if text else ("", False)
        doc_date = preprocess.parse_doc_date(text) if text else None

        info = PageInfo(
            page=page_text.page,
            # 텍스트가 없으면 판정하지 않는다 — VLM 이 렌더 이미지로 판정한다.
            # 여기서 임의로 SPEC 을 붙이면 근거 없는 값을 만드는 것이다(철학 4).
            page_class=PageClass.SPEC if is_spec else PageClass.OTHER,
            tags=preprocess.find_tags(text) if text else [],
            has_text_layer=page_text.has_text_layer,
            text_len=page_text.text_len,
            render_path=rendered.get(page_text.page),
            doc_date=getattr(doc_date, "raw", "") if doc_date else "",
            date_key=getattr(doc_date, "key", None) if doc_date else None,
            date_ambiguous=bool(getattr(doc_date, "ambiguous", False)) if doc_date else False,
            superseded=superseded,
            revision_marker=marker,
            kind_hint=page_text.locator or "",
        )
        infos.append(info)
    return infos


# ══════════════════════════════════════════════════════════════════
#  진입점
# ══════════════════════════════════════════════════════════════════

class Triage:
    """`TriageModule` 구현 — 파이프라인이 `run(path)` 로 호출한다.

    render_dir 을 주면 렌더 결과를 그 폴더에 둔다. 파이프라인이 실행 폴더
    (`runs/<id>/`)를 지정하면 화면이 그 경로로 이미지를 찾는다.
    """

    def __init__(self, render_dir: str | None = None) -> None:
        """렌더 위치를 받아 둔다 (None 이면 임시 폴더)."""
        self.render_dir = render_dir

    def run(self, path: str) -> TriageResult:
        """파일 하나를 판정한다. 예외를 던지지 않는다.

        입력  : path — 파일 경로
        출력  : TriageResult
        부수효과: 파일을 읽고 페이지를 렌더한다(쓰기는 렌더 결과뿐).
        """
        return triage(path, render_dir=self.render_dir)


def triage(path: str, render_dir: str | None = None) -> TriageResult:
    """① Triage 본체 — 공용 도구를 순서대로 조립해 판정한다.

    입력  : path — 파일 경로, render_dir — 렌더 결과 위치
    출력  : TriageResult (document_class · targets · pages · reason · stats)
    부수효과: 파일 읽기 + 페이지 렌더. **예외를 던지지 않는다** — 미지원·실패도
             DocumentClass 로 표현하고 사유를 reason 에 남긴다(철학 5).
    """
    info = match_filename_pattern(path)                  # ① 파일명 (95.5% 가 여기서 해결)
    base = {
        "source_path": path,
        "file_tag": info.tag,
        "file_doc_kind": info.doc_kind,
        "file_rev": info.rev,
    }

    # ── 미지원 포맷 — 예외 대신 UNSUPPORTED ────────────────────────
    if not info.supported_ext:
        return TriageResult(
            document_class=DocumentClass.UNSUPPORTED,
            confidence=CONFIDENCE_UNSUPPORTED,
            reason=f"미지원 포맷 {info.ext} — 지원: {sorted(preprocess.SUPPORTED)}",
            stats={"ext": info.ext},
            **base,
        )

    # ── 파일명으로 제외되는 문서 ──────────────────────────────────
    #   공용 scope_reason 만 믿는다. 자체 키워드 목록을 두면 공용 결정과 어긋난다 —
    #   정비·개조 보고서는 대상이고, 그것을 배제한 것이 이전 구현의 결함이었다.
    out_reason = preprocess.scope_reason(path)
    if out_reason:
        return TriageResult(
            document_class=DocumentClass.OUT_OF_SCOPE,
            confidence=CONFIDENCE_OUT_OF_SCOPE,
            reason=f"{out_reason} (파일명 판정)",
            stats={"ext": info.ext},
            **base,
        )

    # ── 페이지 탐침 · 렌더 · 판정 ─────────────────────────────────
    page_texts, stats = probe_structure(path)
    pages = classify_pages(path, page_texts, render_dir=render_dir)
    stats["rendered_pages"] = sum(1 for p in pages if p.render_path)
    stats["spec_pages"] = sum(1 for p in pages if p.is_spec)
    # 사양표가 여러 페이지 중 일부면 embedded 다 (다른 문서에 사양표가 섞임).
    embedded = (stats["page_count"] > 1
                and 0 < stats["spec_pages"] < stats["page_count"])

    caution = preprocess.caution_reason(path)            # 정비 보고서 등의 주의 문구

    # ── 최신성으로 사양표 한 장 선택 ──────────────────────────────
    #   태그 단독성이 아니다 — 10FV011 에서 그 규칙은 1986 폐기본을 고른다.
    selected, pick_reason = preprocess.pick_latest_spec(pages, info.tag)
    if selected is not None:
        selected.selected = True
        selected.reason = pick_reason

    reason_parts = [pick_reason]
    if caution:
        reason_parts.append(caution)
    if stats.get("mixed_text_scan"):
        reason_parts.append(
            f"텍스트·스캔 혼재 ({stats['text_pages']}/{stats['page_count']}페이지에 텍스트) "
            f"— Router 는 페이지 단위로 경로를 정해야 한다")
    if stats.get("probe_error"):
        reason_parts.append(f"탐침 실패: {stats['probe_error']}")

    # ── 사양표를 못 고른 경우 ─────────────────────────────────────
    #   두 경우를 구분한다. 섞으면 지시서가 금지한 임의 선택이 된다.
    if selected is None:
        candidates = [p for p in pages if p.is_spec]

        # (가) 후보는 있는데 최신성으로 못 가렸다 → **고르지 않는다.**
        #      지시서 91행 "pick_latest_spec() 이 None 이면 고르지 말고 확인필요로",
        #      118행 "못 가릴 때 아무거나 고르지 말 것 — None + 사유가 정답이다".
        #      실물 6건이 여기 온다(070100 · 19FV023030 · 19LV003AB · 20FV905 ·
        #      30TV103). 070100 은 서로 다른 밸브 2대(B10-TV-040 · B10-TV-1016)가
        #      든 견적서라 한 장을 고르면 다른 설비의 값을 이 태그의 값으로 만든다.
        if candidates:
            pages_text = ", ".join(f"p{c.page}" for c in candidates)
            return TriageResult(
                document_class=(DocumentClass.DATASHEET_EMBEDDED if embedded
                                else DocumentClass.DATASHEET),
                targets=[],                              # 비워 둔다 — 고르지 않았다
                confidence=CONFIDENCE_AMBIGUOUS_SPEC,
                pages=pages,
                # "자산 N건 발견" 근거. 고르지 않았으므로 후보의 태그를 모두 남긴다.
                extra_assets=_extra_assets(pages, None, info.tag),
                reason=" | ".join(reason_parts + [
                    f"사양표 후보 {len(candidates)}장({pages_text}) 중 최신본을 가리지 "
                    f"못함 — 페이지를 고르지 않는다. 사람이 선택해야 한다"]),
                stats=stats,
                **base,
            )

        # (나) 후보가 0장 = 텍스트 근거 자체가 없다 (스캔 문서 — 코퍼스의 85.2%).
        #      여기서 고르지 않으면 VLM 주 경로가 죽는다(지시서 55행 "tif 734건
        #      전부 스캔 → VLM"). 할 일 5(make_montage VLM 이진 판정)가 구현되면
        #      이 분기 자체가 없어진다 — 그때까지 1페이지를 잠정 대상으로 두고
        #      확신도를 낮게 준다. **미해결 사항으로 팀 협의 대상이다.**
        has_render = stats["rendered_pages"] > 0
        if has_render and info.in_scope is not False:
            # 파일명이 대상이라고 말하고 렌더도 됐다 — VLM 이 페이지를 판정한다.
            first = pages[0] if pages else None
            if first is not None:
                first.selected = True                    # 1페이지를 잠정 대상으로
                first.reason = "사양표 미판정 — VLM 이 렌더 이미지로 판정해야 한다"
            return TriageResult(
                document_class=DocumentClass.DATASHEET,
                targets=_targets(first),
                confidence=CONFIDENCE_FILENAME_ONLY,
                pages=pages,
                reason=" | ".join(reason_parts + [
                    "텍스트 근거 없음 — 파일명이 대상이고 렌더가 되었으므로 VLM 판정으로 넘긴다"]),
                stats=stats,
                **base,
            )
        return TriageResult(
            document_class=DocumentClass.OUT_OF_SCOPE,
            confidence=CONFIDENCE_NO_SPEC,
            pages=pages,
            reason=" | ".join(reason_parts + ["사양표를 찾지 못하고 렌더도 실패 — 사람이 확인"]),
            stats=stats,
            **base,
        )

    # ── 사양표를 골랐다 ──────────────────────────────────────────
    return TriageResult(
        document_class=(DocumentClass.DATASHEET_EMBEDDED if embedded
                        else DocumentClass.DATASHEET),
        targets=_targets(selected),
        confidence=CONFIDENCE_SPEC_SELECTED,
        pages=pages,
        extra_assets=_extra_assets(pages, selected, info.tag),
        reason=" | ".join(reason_parts),
        stats=stats,
        **base,
    )


def _targets(page: PageInfo | None) -> list[Target]:
    """선택된 페이지를 `Target` 목록으로 만든다 (MVP 는 1파일 = 1자산)."""
    if page is None:
        return []
    return [Target(page_from=page.page, page_to=page.page, expected_tag_count=1)]


def _extra_assets(pages: list[PageInfo], selected: PageInfo | None,
                  file_tag: str | None) -> list[str]:
    """이번에 처리하지 않은 태그를 모은다 (화면에는 띄우지 않고 로그로만).

    "1,089건 중 N건이 다중 자산" 이라는 본개발 근거가 이 값에서 나온다.

    `selected` 가 None 이면(후보를 못 가려 고르지 않은 경우) 후보의 태그를
    모두 남긴다 — 그때가 "자산 N건 발견" 을 띄워야 하는 상황이기 때문이다.

    **사양표 페이지에 있는 태그만** 센다. 공용 `find_tags` 는 태그 정규식에 걸리는
    것을 모두 돌려주는데, 실측에서 재질(`316SS`) · 날짜(`1SEP2021`) · 수치(`250RA83`)
    가 태그로 잡혔다. 그 오탐을 다중 자산 통계에 넣으면 근거가 오염된다.
    사양표가 아닌 페이지의 태그는 자산 근거로 쓰지 않는다 — 두 번째 자산이라면
    자기 사양표를 갖고 있어야 한다.
    """
    seen: list[str] = []
    own = set(selected.tags or []) if selected is not None else set()
    for page in pages:
        if not page.is_spec:                             # 사양표가 아닌 페이지는 근거로 쓰지 않는다
            continue
        for tag in (page.tags or []):
            if tag in own or tag in seen:
                continue
            if file_tag and preprocess.normalize_tag(tag) == file_tag:
                continue                                 # 이 파일의 주 자산은 제외
            seen.append(tag)
    return seen
