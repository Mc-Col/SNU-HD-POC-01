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
            session.origin("vlm")
            session.go(session.UPLOAD)

        from src import env
        has_key = env.load()
        if not has_key:
            st.warning("API 키가 없어 VLM 판독을 쓸 수 없습니다. "
                       "`.env` 에 OPENAI_API_KEY 를 넣고 스트림릿을 재시작하세요.")
        use_vlm = st.toggle(
            "VLM 으로 판독 (API 사용)", value=has_key, disabled=not has_key,
            key="tg_vlm",
            help="끄면 규칙 경로만 씁니다. 스캔 문서는 값이 나오지 않습니다 — "
                 "대상의 71.9% 가 스캔이기 때문입니다.")
        session.set_use_vlm(bool(use_vlm))
        meta = schema.summary()
        only_mvp = st.toggle(
            f"MVP {meta['mvp']}필드만 판독", value=session.only_mvp(),
            key="tg_mvp",
            help=f"끄면 전체 {meta['field_count']}필드를 뽑습니다(기본). "
                 f"켜면 토큰과 검토 시간이 줄지만, 발표 숫자가 전체 기준이라 "
                 f"시연도 전체로 두는 편이 설명이 쉽습니다.")
        session.set_only_mvp(bool(only_mvp))
        st.caption("읽을 쪽은 다음 화면에서 지면을 보고 고릅니다.")

        st.markdown("---")
        st.caption("모듈이 붙기 전에도 화면을 검증할 수 있게 합성 픽스처를 둡니다. "
                   "회사 문서를 쓰지 않습니다.")
        if st.button("합성 픽스처로 화면 보기", type="primary", key="btn_fixture"):
            session.pending(source.ensure_fixture_page())
            session.origin("fixture")
            session.go(session.CONFIRM)
        if st.button("합성 다중 페이지 — 쪽 고르기 시연", key="btn_fixture_multi",
                     help="사양표가 2장이고 하나는 1986년 폐기본입니다. "
                          "폐기 표시가 손글씨라 축소 이미지로는 가릴 수 없습니다"):
            session.pending(source.ensure_fixture_page(multi=True))
            session.origin("fixture")
            session.go(session.CONFIRM)

        st.markdown("---")
        st.caption("실행이 끝난 뒤 어휘 밖 값을 **한 번에** 승인합니다 — "
                   "문서마다 물으면 읽지 않고 승인하게 됩니다.")
        if st.button("사전 승인 화면", key="btn_approve_open"):
            session.go(session.APPROVE)

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


# ── 3 · 쪽 고르기 ─────────────────────────────────────────────

def confirm_screen() -> None:
    """어느 쪽을 판독할지 사람이 고른다. 구현은 `pages.py` 에 있다.

    쪽 선택을 사람이 하기로 정했으므로(2026-08-25) 이 화면은 파이프라인의
    일부다 — 자동 선별에 쓰던 문서당 3,500 토큰이 여기서 사라진다.
    """
    from src.ui import pages

    path = session.pending()
    if not path:
        session.go(session.MAIN)
    pages.render(path)


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
        origin = session.origin()
        if origin == "fixture":
            d = source.from_fixture(page_path=path, page=session.page())
        elif origin == "vlm" and session.use_vlm():
            d = source.from_vlm(path, page=session.page(),
                                only_mvp=session.only_mvp())
        else:
            d = source.from_pipeline(path, only_mvp=session.only_mvp())
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
        if st.button("사전 승인 화면", use_container_width=True, key="btn_approve"):
            session.go(session.APPROVE)

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
