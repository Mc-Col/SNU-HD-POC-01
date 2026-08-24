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

# 화면 6단계 — 화면정의서와 같은 순서
MAIN, UPLOAD, CONFIRM, EXTRACT, HITL, DONE = (
    "main", "upload", "confirm", "extract", "hitl", "done")

_STAGE = "stage"
_DOC = "doc"
_SEL = "selected_field"
_PENDING = "pending_file"
_ORIGIN = "origin"
_RUN = "run_started"
_ONLY_UNRESOLVED = "only_unresolved"
_WHO = "reviewer"


def init() -> None:
    ss = st.session_state
    ss.setdefault(_STAGE, MAIN)
    ss.setdefault(_DOC, None)
    ss.setdefault(_SEL, None)
    ss.setdefault(_PENDING, None)
    ss.setdefault(_ORIGIN, "fixture")
    ss.setdefault(_ONLY_UNRESOLVED, False)
    ss.setdefault(_WHO, "")


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


def reviewer() -> str:
    """검토자 이름. 사람의 판단과 규칙 메모에 누가 썼는지 남기기 위한 것."""
    return (st.session_state.get(_WHO) or "").strip() or "reviewer"


def only_unresolved(value: bool | None = None) -> bool:
    if value is not None:
        st.session_state[_ONLY_UNRESOLVED] = bool(value)
    return bool(st.session_state[_ONLY_UNRESOLVED])


# ── 로그 ──────────────────────────────────────────────────────

def _start_run(d: UiDoc) -> None:
    """문서 1건당 검증 세션 로그를 하나 연다. runs/hitl-<doc_id>/ 에 append."""
    ss = st.session_state
    if ss.get(_RUN) == d.result.doc_id:
        return
    try:
        hooks.start_run(
            f"hitl-{d.result.doc_id}", schema.config_hashes(),
            {"stage": "hitl", "origin": d.origin, "source": d.display_name,
             "fields": len(d.records)},
        )
        ss[_RUN] = d.result.doc_id
    except Exception:
        pass          # 로깅 실패가 화면을 죽이지 않는다


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


def set_page(n: int) -> None:
    st.session_state["vlm_page"] = max(1, int(n))


def page() -> int:
    return int(st.session_state.get("vlm_page", 1))
