# -*- coding: utf-8 -*-
"""화면 1·2·3·4·6 — 화면정의서의 6단계를 그대로 유지한다.

실질은 업로드 → 처리 → 검증 → 완료 네 국면이지만, 사용자가 멈춰서 확인하는
지점(3 확인화면)을 접어버리면 발표에서 설명할 자리가 없어진다.

추출 화면은 더미 지연으로 돈다. 잠금 로직과 화면 흐름을 파서 없이 검증하는 것이
이 단계의 목적이고, 실제 파이프라인은 원천 토글로 바로 붙는다.
"""
from __future__ import annotations

import os
import tempfile
import time

import streamlit as st

from src import schema
from src.hooks import hooks
from src.ui import export, session, source

ACCEPT = ["xlsx", "xlsm", "xls", "pdf", "tif", "tiff", "jpg", "jpeg", "png"]

# 진행 표시 — 파이프라인 단계 이름을 그대로 쓴다. spinner 만 돌리면 흐름이 안 보인다
STEPS = [
    "① 분류 — 이 파일에 자산 데이터가 있는가",
    "② 경로 선택 — 텍스트 레이어 탐침",
    "③ 추출 — 값 · 위치 · 확신도",
    "④ 정규화 — 도메인 규칙 적용",
    "⑤ 검증 — 형식 → 도메인 → 평가 Agent",
    "⑥ 판정 — 정상추출 / 확인필요 / N/A",
]
STEP_DELAY = 0.45          # 고정값. 난수를 쓰지 않는다


# ── 1 · 메인 ──────────────────────────────────────────────────

def main_screen() -> None:
    st.title("Datasheet 정보추출 Agent (PoC)")
    st.caption("비정형 설비 문서에서 컨트롤밸브 기준정보를 추출·검증해 "
               "마스터 스키마 엑셀로 내보냅니다. 최종 확정은 사람이 합니다.")

    left, right = st.columns([1.25, 1], gap="large")

    with left:
        up = st.file_uploader("파일을 여기로 끌어놓으세요 또는 파일 선택",
                              type=ACCEPT, key="uploader")
        if up is not None:
            path = _stash(up)
            session.pending(path)
            session.origin("pipeline")
            session.go(session.UPLOAD)

        st.markdown("---")
        st.caption("모듈이 붙기 전에도 화면을 검증할 수 있게 합성 픽스처를 둡니다. "
                   "회사 문서를 쓰지 않습니다.")
        if st.button("합성 픽스처로 화면 보기", type="primary", key="btn_fixture"):
            session.pending(source.ensure_fixture_page())
            session.origin("fixture")
            session.go(session.CONFIRM)

    with right:
        meta = schema.summary()
        st.markdown("**현재 지원되는 포맷**")
        st.markdown("- Excel\n- PDF (텍스트)\n- PDF (이미지)\n- TIF")
        st.markdown("**현재 지원되는 설비 종류**")
        st.markdown("- Control Valve")
        st.caption(
            f"필드 정의 {meta['field_count']}개 · 필수 {meta['required']}개 · "
            f"MVP 대상 {meta['mvp']}개 — schema/fields.yaml 에서 읽습니다")


# ── 2 · 업로드 ────────────────────────────────────────────────

def upload_screen() -> None:
    path = session.pending()
    if not path:
        session.go(session.MAIN)
    st.subheader("업로드 중")
    st.markdown(f"<span class='d2s-code'>{os.path.basename(path)} "
                f"({os.path.getsize(path) // 1000} kB)</span>",
                unsafe_allow_html=True)
    bar = st.progress(0.0)
    for i in range(5):
        time.sleep(0.08)
        bar.progress((i + 1) / 5)
    session.go(session.CONFIRM)


# ── 3 · 확인 ──────────────────────────────────────────────────

def confirm_screen() -> None:
    path = session.pending()
    if not path:
        session.go(session.MAIN)

    st.subheader("확인")
    name = ("19-FV-001.pdf (합성 픽스처)" if session.origin() == "fixture"
            else os.path.basename(path))
    st.markdown(f"<span class='d2s-code'>{name} "
                f"({os.path.getsize(path) // 1000} kB)</span>",
                unsafe_allow_html=True)
    st.caption("이 파일을 처리합니다. 30필드 중 MVP 대상 필드만 먼저 뽑습니다.")

    c1, c2, _ = st.columns([1, 1, 3])
    if c1.button("변환 시작 ≫", type="primary", use_container_width=True,
                 key="btn_start"):
        session.go(session.EXTRACT)
    if c2.button("≪ 돌아가기", use_container_width=True, key="btn_back"):
        session.reset()


# ── 4 · 추출 ──────────────────────────────────────────────────

def extract_screen() -> None:
    path = session.pending()
    if not path:
        session.go(session.MAIN)

    st.subheader("데이터 추출 중")
    bar = st.progress(0.0)
    slot = st.empty()

    for i, label in enumerate(STEPS):
        slot.markdown(f"<div class='d2s-raw'>{label}</div>", unsafe_allow_html=True)
        time.sleep(STEP_DELAY)
        bar.progress((i + 1) / len(STEPS))

    try:
        if session.origin() == "fixture":
            d = source.from_fixture()
        else:
            d = source.from_pipeline(path)
    except Exception as e:                      # 실패를 삼키지 않는다
        hooks.on_error(None, "ui.extract", e)
        st.error(f"추출 실패 — {type(e).__name__}: {e}")
        if st.button("처음으로", key="btn_home"):
            session.reset()
        return

    session.set_doc(d)
    session.go(session.HITL)


# ── 6 · 완료 ──────────────────────────────────────────────────

def done_screen() -> None:
    d = session.doc()
    if d is None:
        session.go(session.MAIN)

    st.success("추출이 성공적으로 완료되었습니다")
    res = d.result
    counts = res.counts()
    edited = sum(1 for r in res.records if r.human_value is not None)

    c = st.columns(4)
    c[0].metric("필드", len(res.records))
    c[1].metric("정상추출", counts.get("auto", 0))
    c[2].metric("사람이 확인·수정", sum(1 for r in res.records if r.human_action))
    c[3].metric("값이 바뀐 필드", edited)

    st.markdown("---")
    c1, c2, c3 = st.columns([1.3, 1.5, 1])
    with c1:
        st.download_button("추출결과 (.xlsx) 다운로드", data=export.build(d),
                           key="btn_download",
                           file_name=export.filename(d), type="primary",
                           mime="application/vnd.openxmlformats-officedocument."
                                "spreadsheetml.sheet",
                           use_container_width=True)
    with c2:
        st.button("설비통합플랫폼으로 보내기", disabled=True, use_container_width=True,
                  help="1단계 PoC 범위 외")
        st.caption("범위 외 — 1단계는 엑셀 산출까지입니다")
    with c3:
        if st.button("새로운 작업 시작", use_container_width=True, key="btn_new"):
            session.reset()

    with st.expander("무엇이 기록되었나"):
        st.caption(f"검증 이력은 runs/hitl-{res.doc_id}/ 에 남습니다 — "
                   f"events.jsonl(사람의 판단) · records.jsonl(필드 확정값). "
                   f"Loop C(규칙 개선)와 자동확정률·오적재율 집계가 이 로그를 씁니다.")
        st.json({"doc_id": res.doc_id, "counts": counts,
                 "approvable": res.approvable,
                 "config_hashes": schema.config_hashes()}, expanded=False)


# ── 업로드 파일 보관 ──────────────────────────────────────────

def _stash(uploaded) -> str:
    """업로드 파일은 시스템 임시 폴더에 둔다. 저장소 트리에 회사 문서를 두지 않는다."""
    tmp = st.session_state.get("tmpdir")
    if not tmp or not os.path.isdir(tmp):
        tmp = tempfile.mkdtemp(prefix="d2s_ui_")
        st.session_state["tmpdir"] = tmp
    path = os.path.join(tmp, uploaded.name)
    with open(path, "wb") as f:
        f.write(uploaded.getbuffer())
    return path
