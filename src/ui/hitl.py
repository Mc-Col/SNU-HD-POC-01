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

    n = overlay.page_count(d.page_path)
    if n > 1:
        # 원본을 그대로 띄운 경우(다중 페이지 PDF) — 쪽을 넘길 수 있다
        page = filt = st.number_input("페이지", 1, n, min(d.page_no, n),
                                      key="hitl_page")
    else:
        # 한 쪽만 떠 온 경우(스캔은 항상 그렇다). 이미지에는 쪽 번호가 없으니
        # 무슨 쪽을 떴는지는 UiDoc 이 알고 있다 — 그 쪽의 박스만 그린다.
        page, filt = 1, d.page_no

    boxes = overlay.boxes_for(d.records, filt, sel)
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

    # 값 — 안 바뀐 것은 하나만, 바뀐 것은 `원문 → 표준값` 한 줄로
    with c[1]:
        val = rec.final_value or "—"
        raw = rec.raw_value
        if raw and str(raw).strip() and str(raw) != str(rec.final_value):
            st.markdown(f"<div class='d2s-val'><span class='d2s-was'>{raw}</span>"
                        f" → {val}</div>", unsafe_allow_html=True)
        else:
            st.markdown(f"<div class='d2s-val'>{val}</div>", unsafe_allow_html=True)

        # 표시원 — 어느 수단이 이 칸을 불렀는가. 매번 다시 계산된다
        flags = d.flags(rec)
        if flags:
            st.markdown(" ".join(theme.flag_badge(x.source) for x in flags),
                        unsafe_allow_html=True)
        elif rec.note:
            # 표시원이 있으면 배지가 이미 말한다. 없을 때만 비고를 한 줄로.
            st.markdown(f"<div class='d2s-note1'>{rec.note}</div>",
                        unsafe_allow_html=True)
        _evidence(rec, flags)

    c[2].markdown(f"<div class='d2s-req'>{'필수' if rec.required else '—'}</div>",
                  unsafe_allow_html=True)

    with c[3]:
        st.markdown(theme.chip(rec.state) + theme.SAFETY_TAG.get(rec.safety, ""),
                    unsafe_allow_html=True)

    with c[4]:
        _actions(d, rec)

    st.markdown("<div class='d2s-row'></div>", unsafe_allow_html=True)


def _evidence(rec: FieldRecord, flags) -> None:
    """왜 이 상태인지 · 어떻게 변환됐는지 · 이 필드의 판단 지침 — 하나로 접는다.

    행마다 사유 문장이 붙으면 14행을 훑는 일이 읽기가 되고, 그러면 정상추출을
    패스해서 얻는 이득이 사라진다. 한눈에 필요한 것은 값·상태·무엇이 걸렸나 셋뿐이다.
    """
    g = schema.guidance(rec.field_key) or {}
    if not (flags or rec.transform_trace or rec.retry_count or rec.note
            or g.get("text") or not rec.resolved):
        return                                  # 볼 것이 없으면 버튼도 없다

    with st.popover("근거", use_container_width=True):
        st.markdown(f"**{rec.field_name}**")
        st.caption(f"확신도 {rec.confidence:.2f} · 임계 {rec.threshold:.2f}"
                   + (f" · 재시도 {rec.retry_count}회" if rec.retry_count else ""))
        for x in flags:
            st.markdown(f"- **{x.source}** — {x.why}")
        if rec.note:
            st.markdown(f"- 비고 — {rec.note}")
        for t in rec.transform_trace:
            st.markdown(f"- {t}")
        if rec.retry_values:
            st.caption("재시도 값 비교: " + " / ".join(rec.retry_values))
        st.markdown("---")
        _guidance(rec, g)


def _guidance(rec: FieldRecord, g: dict) -> None:
    """자연어 판단 지침 — 읽고 그 자리에서 쓴다. `[근거]` 안에 들어 있다.

    저장하면 `schema/guidance.yaml` 에 들어가고 `on_rule_edit` 로 로그에 남는다.
    """
    if g.get("text"):
        st.markdown(f"<div class='d2s-guide'>{g['text']}</div>",
                    unsafe_allow_html=True)
        st.caption(f"판단 지침 · {g.get('by', '—')} · {g.get('updated', '—')}")
    else:
        st.caption("이 항목의 판단 지침이 아직 없습니다. 남기면 다음 검토자가 "
                   "같은 고민을 반복하지 않습니다.")
    new = st.text_area("판단 지침", value=g.get("text", ""),
                       key=f"gd_{rec.field_key}", height=120,
                       label_visibility="collapsed")
    if st.button("지침 저장", key=f"gdsave_{rec.field_key}"):
        who = session.reviewer()
        before = schema.set_guidance(rec.field_key, new, by=who,
                                     today=date.today().isoformat())
        hooks.on_rule_edit(rec.field_key, before, new.strip(), by=who)
        st.toast(f"{rec.field_name} 지침 저장 — schema/guidance.yaml")
        st.rerun()


def _actions(d: UiDoc, rec: FieldRecord) -> None:
    """조치는 행마다 최대 두 개. **수정은 모든 행에서 가능하다.**

    정상추출은 "확인을 생략할 수 있다" 는 뜻이고 "수정할 수 없다" 는 뜻이 아니다.
    확신도 0.98 로 안전 필드가 뒤집힌 실측 사례가 있고 어느 수단도 그것을 잡지
    못했다. 그리고 **자동확정 값을 사람이 고친 건수가 곧 오적재 관측치다** —
    이 경로를 막으면 오적재율이 영원히 0으로 보인다.
    """
    if rec.human_action:
        st.markdown(f"<div class='d2s-raw'>✓ "
                    f"{theme.ACTION.get(rec.human_action, rec.human_action)}</div>",
                    unsafe_allow_html=True)
        if st.button("되돌리기", key=f"undo_{rec.field_key}"):
            rec.human_action = None
            rec.human_value = None
            rec.approved_by = None
            st.rerun()
        return

    if rec.state is FieldState.AUTO:
        if rec.safety != "normal":
            # 안전·식별 필드는 정상추출이어도 눈으로 한 번 본다
            a, b = st.columns([1, 1])
            with a:
                if st.button("확인", key=f"ok_{rec.field_key}", type="primary"):
                    session.apply_human(rec, "approve")
                    st.rerun()
            with b:
                _edit(rec)
        else:
            _edit(rec)
        return

    # 확인필요 · 근거없음 — 이미 입력칸이 열려 있다
    typed = st.text_input("직접 입력", key=f"in_{rec.field_key}",
                          value="", placeholder=rec.value or "값 입력",
                          label_visibility="collapsed")
    b1, b2 = st.columns([1, 1])
    with b1:
        label = "N/A 확인" if rec.state is FieldState.NA else "확인"
        if st.button(label, key=f"go_{rec.field_key}", type="primary"):
            if typed.strip():
                session.apply_human(rec, "override", typed.strip())
            elif rec.state is FieldState.NA:
                session.apply_human(rec, "na_confirm")
            else:
                session.apply_human(rec, "approve")
            st.rerun()
    with b2:
        # 계산으로 채우는 것은 운전조건 기준 Cv 다. 밸브 정격(rated_cv)이 아니다.
        if rec.field_key == "required_cv" and rec.state is FieldState.NA:
            with st.popover("Cv 계산"):
                cv.panel(d, rec)


def _edit(rec: FieldRecord) -> None:
    """AI 가 확신한 값도 사람이 고칠 수 있다. 팝오버 한 겹이 실수를 막는다."""
    with st.popover("수정", use_container_width=True):
        st.markdown(f"**{rec.field_name}**")
        if rec.raw_value and str(rec.raw_value) != str(rec.final_value):
            st.caption(f"원문 {rec.raw_value}")
        new = st.text_input("값", value=rec.final_value or "",
                            key=f"ed_{rec.field_key}")
        if st.button("이 값으로 확정", key=f"edok_{rec.field_key}",
                     type="primary"):
            if new.strip() and new.strip() != (rec.final_value or ""):
                # state 는 그대로 둔다 — AUTO 였다는 사실이 남아야 오적재를 센다
                rec.note = (rec.note + " | 사람이 추출값을 수정").strip(" |")
                session.apply_human(rec, "override", new.strip())
            else:
                session.apply_human(rec, "approve")
            st.rerun()


# ── 하단 · 잠금 ───────────────────────────────────────────────

def _footer(d: UiDoc) -> None:
    res = d.result
    left = d.unresolved_required

    c1, c2, c3 = st.columns([1, 1.4, 3])
    with c1:
        # 재추출은 **문서 전체**다 (2026-08-25 결정). 필드 단위 재판독은
        # Loop A 의 몫이고 화면 버튼이 아니다.
        paid = d.origin == "vlm"
        if st.button("↻ 재추출", use_container_width=True, key="btn_reextract",
                     help="문서 전체를 다시 처리합니다. 사람의 수정은 사라지고"
                          + (", VLM 을 다시 호출합니다(비용 발생)" if paid else "")):
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
