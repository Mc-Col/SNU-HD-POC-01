# -*- coding: utf-8 -*-
"""화면 단계 기계 + 사람의 판단 기록.

Streamlit 은 위젯을 건드릴 때마다 스크립트 전체를 다시 돈다. 그래서
`DocumentResult` 는 반드시 session_state 에 붙잡아 둔다 — 안 그러면 클릭마다
재추출이 돌고, VLM 을 붙이는 순간 비용과 지연이 그대로 곱해진다.

사람의 판단은 `hooks.on_human_action` 으로 흘려보낸다. 이게 없으면
Loop C(규칙 개선)와 자동확정률·오적재율 KPI 에 넣을 데이터가 생기지 않는다.
"""
from __future__ import annotations

import streamlit as st

from src import schema
from src.contracts import FieldRecord, FieldState
from src.hooks import hooks
from src.ui.source import UiDoc

# 화면 6단계 — 화면정의서와 같은 순서. APPROVE 는 흐름 밖의 별도 화면이다
# (문서 하나가 아니라 **실행 전체**의 후보를 한 번에 본다).
MAIN, UPLOAD, CONFIRM, EXTRACT, HITL, DONE = (
    "main", "upload", "confirm", "extract", "hitl", "done")
APPROVE = "approve"

_STAGE = "stage"
_DOC = "doc"
_SEL = "selected_field"
_PENDING = "pending_file"
_ORIGIN = "origin"
_RUN = "run_started"
_ONLY_UNRESOLVED = "only_unresolved"
_WHO = "reviewer"
_CANDS = "page_candidates"


def init() -> None:
    ss = st.session_state
    ss.setdefault(_STAGE, MAIN)
    ss.setdefault(_DOC, None)
    ss.setdefault(_SEL, None)
    ss.setdefault(_PENDING, None)
    ss.setdefault(_ORIGIN, "fixture")
    ss.setdefault(_ONLY_UNRESOLVED, False)
    ss.setdefault(_WHO, "")
    ss.setdefault(_CANDS, [])


def stage() -> str:
    return st.session_state[_STAGE]


def go(next_stage: str) -> None:
    st.session_state[_STAGE] = next_stage
    st.rerun()


def reset() -> None:
    for k in (_DOC, _SEL, _PENDING):
        st.session_state[k] = None
    st.session_state[_STAGE] = MAIN
    st.rerun()


# ── 문서 ──────────────────────────────────────────────────────

def doc() -> UiDoc | None:
    return st.session_state.get(_DOC)


def set_doc(d: UiDoc) -> None:
    st.session_state[_DOC] = d
    st.session_state[_SEL] = None
    _start_run(d)


def pending(path: str | None = None):
    if path is not None:
        st.session_state[_PENDING] = path
    return st.session_state.get(_PENDING)


def origin(value: str | None = None) -> str:
    if value is not None:
        st.session_state[_ORIGIN] = value
    return st.session_state[_ORIGIN]


def selected(key: str | None = None, *, toggle: bool = False) -> str | None:
    ss = st.session_state
    if key is not None:
        ss[_SEL] = None if (toggle and ss.get(_SEL) == key) else key
    return ss.get(_SEL)


def page_candidates(v: list[int] | None = None) -> list[int]:
    """사람이 사양표 후보로 지목한 쪽. 규칙과 대조할 때 같은 후보집합을 쓴다."""
    if v is not None:
        st.session_state[_CANDS] = list(v)
    return list(st.session_state.get(_CANDS) or [])


def reviewer() -> str:
    """검토자 이름. 사람의 판단과 규칙 메모에 누가 썼는지 남기기 위한 것."""
    return (st.session_state.get(_WHO) or "").strip() or "reviewer"


def only_unresolved(value: bool | None = None) -> bool:
    if value is not None:
        st.session_state[_ONLY_UNRESOLVED] = bool(value)
    return bool(st.session_state[_ONLY_UNRESOLVED])


# ── 로그 ──────────────────────────────────────────────────────

def ensure_run(key: str, **meta) -> None:
    """검증 세션 로그를 연다. **파일 하나당 하나**이고 여러 번 불러도 안전하다.

    쪽 선택은 추출보다 먼저 일어난다. 그때 실행이 열려 있지 않으면 훅이
    쓸 곳이 없어 이벤트가 조용히 버려진다 — 자동 선택 정확도를 공짜로
    측정하려던 것이 통째로 사라진다. 그래서 파일 이름으로 미리 연다.
    """
    ss = st.session_state
    if not key or ss.get(_RUN) == key:
        return
    try:
        hooks.start_run(f"hitl-{key}", schema.config_hashes(),
                        {"source": key, **meta})
        ss[_RUN] = key
    except Exception:
        pass          # 로깅 실패가 화면을 죽이지 않는다


def _start_run(d: UiDoc) -> None:
    import os
    key = os.path.basename(pending() or "") or d.display_name
    ensure_run(key, stage="hitl", origin=d.origin, fields=len(d.records),
               doc_id=d.result.doc_id)


def apply_human(rec: FieldRecord, action: str, value: str | None = None,
                by: str = "") -> None:
    """사람의 판단을 레코드에 반영하고 hook 으로 남긴다.

    action  approve      추출값 그대로 확정
            override     사람이 값을 고침
            na_confirm   근거 없음을 사람이 확인
    """
    by = by or reviewer()
    before = rec.final_value
    if action == "override":
        rec.human_value = value
    rec.human_action = action
    rec.approved_by = by
    # 검증 세션 소요시간은 스톱워치로 수동 측정 (자동 로깅은 MVP 범위 외)
    hooks.on_human_action(rec.doc_id, rec.field_key, action,
                          before=before, after=rec.final_value, by=by,
                          elapsed_ms=0)


def bulk_approve_auto(d: UiDoc) -> int:
    """정상추출 일괄 패스. 안전·식별 필드는 제외한다 — 눈으로 한 번 봐야 한다."""
    n = 0
    for r in d.records:
        if r.state is FieldState.AUTO and r.safety == "normal" and r.human_action is None:
            apply_human(r, "approve")
            n += 1
    return n

# ── VLM 실행 설정 ─────────────────────────────────────────────
#
#  화면에서 고른 값을 추출 단계까지 실어 보낸다. 기본은 VLM 사용 —
#  대상의 71.9% 가 스캔이므로 규칙 경로만으로는 아무 값도 안 나온다.

def set_use_vlm(v: bool) -> None:
    st.session_state["use_vlm"] = bool(v)


def use_vlm() -> bool:
    return bool(st.session_state.get("use_vlm", True))


def set_only_mvp(v: bool) -> None:
    st.session_state["only_mvp"] = bool(v)


def only_mvp() -> bool:
    """MVP 9필드만 판독할지. **기본은 전체 28필드다** (2026-08-25 결정).

    발표 헤드라인을 28필드로 내기로 했으므로 화면도 28필드를 보여준다 —
    슬라이드는 28인데 시연은 9면 "나머지 19개는?" 을 임원이 묻는다.
    """
    return bool(st.session_state.get("only_mvp", False))


def set_page(n: int) -> None:
    st.session_state["vlm_page"] = max(1, int(n))


def page() -> int:
    return int(st.session_state.get("vlm_page", 1))
