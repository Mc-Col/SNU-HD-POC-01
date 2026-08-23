# -*- coding: utf-8 -*-
"""화면 5 · HITL 검증 — 이 화면이 핵심이다.

좌측 지면 + bbox, 우측 항목 표. 규칙은 하나뿐이다:
**화면은 아무것도 판단하지 않는다.** 상태·필수·임계·잠금조건은 계약과 스키마에서 온다.

- 잠금 조건은 `DocumentResult.approvable` 을 그대로 쓴다 (여기서 계산하지 않는다)
- 정상추출이어도 안전·식별 필드에는 확인 버튼이 나온다
  (`FieldRecord.resolved` 가 safety != normal 을 해소로 인정하지 않는다)
- 원문(`raw_value`)을 표준값과 나란히 둔다 — ATO/ATC 역전을 검증할 수 있는 유일한 근거
"""
from __future__ import annotations

from datetime import date

import streamlit as st

from src import schema
from src.contracts import FieldRecord, FieldState
from src.hooks import hooks
from src.ui import cv, overlay, session, theme
from src.ui.source import UiDoc

COLS = [2.3, 3.2, 0.7, 1.25, 1.9]


def render(d: UiDoc) -> None:
    _sidebar()
    _header(d)

    left, right = st.columns([0.88, 1.12], gap="medium")
    with left:
        _document_pane(d)
    with right:
        _panel(d)

    st.divider()
    _footer(d)


# ── 사이드바 · 검토자와 공통 지침 ─────────────────────────────

def _sidebar() -> None:
    with st.sidebar:
        st.text_input("검토자", key="reviewer", placeholder="이름",
                      help="사람의 판단과 규칙 메모에 누가 썼는지 남깁니다")
        g = schema.general_guidance() or {}
        if g.get("text"):
            st.markdown("###### 검토 원칙")
            st.markdown(f"<div class='d2s-guide'>{g['text']}</div>",
                        unsafe_allow_html=True)
            st.caption(f"schema/guidance.yaml · {g.get('by', '—')}")


# ── 상단 ──────────────────────────────────────────────────────

def _header(d: UiDoc) -> None:
    c1, c2 = st.columns([3, 2])
    with c1:
        st.markdown(f"#### HITL 검증 &nbsp; {theme.chip_counts(d.result.counts())}",
                    unsafe_allow_html=True)
    with c2:
        origin = "픽스처" if d.origin == "fixture" else "파이프라인"
        st.markdown(
            f"<div style='text-align:right' class='d2s-code'>{d.display_name} · "
            f"{d.size_bytes // 1000} kB · 원천 {origin}</div>",
            unsafe_allow_html=True)


# ── 좌측 · 지면과 근거 ────────────────────────────────────────

def _document_pane(d: UiDoc) -> None:
    sel = session.selected()
    if not d.page_path:
        st.info("이 원본은 지면 미리보기를 만들 수 없는 포맷입니다(엑셀 등). "
                "bbox 하이라이트는 PDF·이미지 경로에서만 동작합니다.")
        return

    page = 1
    n = overlay.page_count(d.page_path)
    if n > 1:
        page = st.number_input("페이지", 1, n, 1, key="hitl_page")

    boxes = overlay.boxes_for(d.records, page, sel)
    st.image(overlay.render(d.page_path, page, boxes), use_container_width=True)
    st.markdown(theme.LEGEND, unsafe_allow_html=True)

    if sel:
        rec = d.record(sel)
        if rec and rec.bbox:
            st.caption(f"선택 · {rec.field_name} — {rec.source_locator or '위치 기록 없음'}")
        elif rec:
            st.caption(f"선택 · {rec.field_name} — 지면에 근거 없음. "
                       f"그릴 위치가 없으니 사람이 채워야 한다")
    st.caption(f"경로 · {d.route_reason}" if d.route_reason else "")


# ── 우측 · 항목 표 ────────────────────────────────────────────

def _panel(d: UiDoc) -> None:
    _panel_controls(d)

    head = st.columns(COLS)
    for c, name in zip(head, ["항목", "추출 값", "필수", "상태", "직접 입력 · 확인"]):
        c.markdown(f"<div class='d2s-head'>{name}</div>", unsafe_allow_html=True)

    only_un = session.only_unresolved()
    shown = 0
    for rec in d.records:
        if only_un and rec.resolved:
            continue
        _row(d, rec)
        shown += 1

    if shown == 0:
        st.success("미해소 항목이 없습니다.")

    _guidance_index()


def _guidance_index() -> None:
    """적혀 있는 지침 전체. 도메인 전문가가 한자리에서 훑어볼 수 있어야 한다."""
    all_g = schema.all_guidance()
    with st.expander(f"규칙 메모 — 자연어 지침 {len(all_g)}건 "
                     f"(schema/guidance.yaml)"):
        st.caption("결정론적 규칙은 schema/rules.yaml, 사람 말로 적는 판단 기준은 여기. "
                   "각 항목의 [지침] 버튼에서 바로 쓸 수 있습니다.")
        for key, g in all_g.items():
            try:
                name = schema.get(key).name
            except KeyError:
                name = f"{key} (스키마에 없는 키)"
            st.markdown(f"**{name}** &nbsp; <span class='d2s-code'>{key}</span>",
                        unsafe_allow_html=True)
            st.markdown(f"<div class='d2s-guide'>{g.get('text', '')}</div>",
                        unsafe_allow_html=True)
            st.caption(f"작성 {g.get('by', '—')} · {g.get('updated', '—')}")


def _panel_controls(d: UiDoc) -> None:
    c1, c2 = st.columns([1.2, 1])
    passable = [r for r in d.records
                if r.state is FieldState.AUTO and r.safety == "normal"
                and r.human_action is None]
    with c1:
        if passable and st.button(f"정상추출 {len(passable)}개 일괄 승인",
                                  use_container_width=True, key="btn_bulk"):
            n = session.bulk_approve_auto(d)
            st.toast(f"{n}개 일괄 승인 — 안전·식별 필드는 제외됩니다")
            st.rerun()
    with c2:
        v = st.toggle("미해소만 보기", value=session.only_unresolved(),
                      key="tg_unresolved")
        if v != session.only_unresolved():
            session.only_unresolved(v)
            st.rerun()


def _row(d: UiDoc, rec: FieldRecord) -> None:
    sel = session.selected() == rec.field_key
    c = st.columns(COLS, vertical_alignment="center")

    # 항목 — 누르면 지면에서 그 위치가 굵어진다
    with c[0]:
        if st.button(rec.field_name, key=f"sel_{rec.field_key}",
                     type="primary" if sel else "secondary",
                     use_container_width=True,
                     help="지면에서 근거 위치 보기"):
            session.selected(rec.field_key, toggle=True)
            st.rerun()

    # 값 — 표준값 위에, 원문을 아래에 병기
    with c[1]:
        val = rec.final_value or "—"
        st.markdown(f"<div class='d2s-val'>{val}</div>", unsafe_allow_html=True)
        if rec.raw_value and rec.raw_value != rec.final_value:
            st.markdown(f"<div class='d2s-raw'>원문 {rec.raw_value}</div>",
                        unsafe_allow_html=True)
        if rec.note:
            st.markdown(f"<div class='d2s-note'>{rec.note}</div>",
                        unsafe_allow_html=True)
        pc = st.columns([1, 1])
        with pc[0]:
            if rec.transform_trace or rec.retry_count:
                with st.popover("이력", use_container_width=True):
                    st.caption(f"확신도 {rec.confidence:.2f} · 임계 {rec.threshold:.2f} "
                               f"· 재시도 {rec.retry_count}회")
                    for t in rec.transform_trace:
                        st.markdown(f"- {t}")
                    if rec.retry_values:
                        st.caption("재시도 값 비교: " + " / ".join(rec.retry_values))
        with pc[1]:
            if schema.guidance(rec.field_key) or not rec.resolved:
                _guidance(rec)

    c[2].markdown(f"<div class='d2s-req'>{'필수' if rec.required else '—'}</div>",
                  unsafe_allow_html=True)

    with c[3]:
        st.markdown(theme.chip(rec.state), unsafe_allow_html=True)
        if rec.safety != "normal":
            st.markdown(f"<div class='d2s-code'>{rec.safety}</div>",
                        unsafe_allow_html=True)

    with c[4]:
        _actions(d, rec)

    st.markdown("<div class='d2s-row'></div>", unsafe_allow_html=True)


def _guidance(rec: FieldRecord) -> None:
    """자연어 판단 지침 — 읽고, 그 자리에서 쓴다.

    결정론적 규칙으로 적을 수 없는 판단 기준이 검토자 머릿속에만 남는 것을 막는다.
    저장하면 `schema/guidance.yaml` 에 들어가고 `on_rule_edit` 로 로그에 남는다.
    """
    g = schema.guidance(rec.field_key) or {}
    has = bool(g.get("text"))
    with st.popover("지침" if has else "＋지침", use_container_width=True):
        st.markdown(f"**{rec.field_name}**")
        if has:
            st.markdown(f"<div class='d2s-guide'>{g['text']}</div>",
                        unsafe_allow_html=True)
            st.caption(f"작성 {g.get('by', '—')} · {g.get('updated', '—')}")
        else:
            st.caption("아직 적힌 지침이 없습니다. 판단 기준을 남기면 "
                       "다음 검토자가 같은 고민을 반복하지 않습니다.")

        new = st.text_area("판단 지침 (자연어)", value=g.get("text", ""),
                           key=f"gd_{rec.field_key}", height=170,
                           label_visibility="collapsed")
        if st.button("지침 저장", key=f"gdsave_{rec.field_key}", type="primary"):
            who = session.reviewer()
            before = schema.set_guidance(rec.field_key, new, by=who,
                                         today=date.today().isoformat())
            hooks.on_rule_edit(rec.field_key, before, new.strip(), by=who)
            st.toast(f"{rec.field_name} 지침을 저장했습니다 — schema/guidance.yaml")
            st.rerun()


def _actions(d: UiDoc, rec: FieldRecord) -> None:
    if rec.human_action:
        st.markdown(f"<div class='d2s-raw'>✓ {rec.human_action}</div>",
                    unsafe_allow_html=True)
        if st.button("되돌리기", key=f"undo_{rec.field_key}"):
            rec.human_action = None
            rec.human_value = None
            rec.approved_by = None
            st.rerun()
        return

    # 정상추출 + 일반 필드 → 손댈 필요 없음 (일괄 승인 대상)
    if rec.state is FieldState.AUTO and rec.safety == "normal":
        st.markdown("<div class='d2s-raw'>확인 불필요</div>", unsafe_allow_html=True)
        return

    # 안전·식별 필드는 정상추출이어도 눈으로 한 번 본다
    if rec.state is FieldState.AUTO:
        if st.button("확인", key=f"ok_{rec.field_key}"):
            session.apply_human(rec, "approve")
            st.rerun()
        return

    new = st.text_input("직접 입력", key=f"in_{rec.field_key}",
                        value="", placeholder=rec.value or "값 입력",
                        label_visibility="collapsed")
    b1, b2 = st.columns([1, 1])
    with b1:
        label = "N/A 확인" if rec.state is FieldState.NA else "확인"
        if st.button(label, key=f"go_{rec.field_key}", type="primary"):
            if new.strip():
                session.apply_human(rec, "override", new.strip())
            elif rec.state is FieldState.NA:
                session.apply_human(rec, "na_confirm")
            else:
                session.apply_human(rec, "approve")
            st.rerun()
    with b2:
        if rec.field_key == "rated_cv_normal":
            with st.popover("Cv 계산"):
                cv.panel(d, rec)


# ── 하단 · 잠금 ───────────────────────────────────────────────

def _footer(d: UiDoc) -> None:
    res = d.result
    left = d.unresolved_required

    c1, c2, c3 = st.columns([1, 1.4, 3])
    with c1:
        if st.button("↻ 재추출", use_container_width=True, key="btn_reextract",
                     help="같은 문서를 다시 처리한다. 사람의 수정은 사라진다"):
            session.go(session.EXTRACT)
    with c2:
        # 잠금 조건은 계약이 판단한다
        if st.button("검토 완료 ≫", type="primary", disabled=not res.approvable,
                     use_container_width=True, key="btn_approve"):
            session.go(session.DONE)
    with c3:
        if left:
            names = ", ".join(r.field_name for r in left[:4])
            more = f" 외 {len(left) - 4}개" if len(left) > 4 else ""
            st.markdown(
                f"<div class='d2s-note'>필수 필드 {len(left)}개 미해소 — "
                f"{names}{more}. 해소되면 활성화됩니다</div>",
                unsafe_allow_html=True)
        else:
            st.markdown("<div class='d2s-raw'>필수 필드 전부 해소 — 승인 가능</div>",
                        unsafe_allow_html=True)
