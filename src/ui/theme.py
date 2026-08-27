# -*- coding: utf-8 -*-
"""표시 어휘와 스타일 — 계약의 값을 사람 말로 바꾸는 곳.

상태 이름을 여기서 새로 만들지 않는다. `FieldState` 를 한글 라벨에 매핑할 뿐이다.
"""
from __future__ import annotations

import streamlit as st

from src.contracts import FieldState

# 설계도와 같은 색 — 초록 실선 정상추출 / 노란 점선 확인필요 / 적갈 N/A
GREEN = "#2E7D5B"
AMBER = "#A9701C"
MAROON = "#8B4A52"
ACCENT = "#1F4E6B"

LABEL: dict[FieldState, str] = {
    FieldState.AUTO: "정상추출",
    FieldState.REVIEW: "확인필요",
    FieldState.NA: "N/A",
}
COLOR: dict[FieldState, str] = {
    FieldState.AUTO: GREEN,
    FieldState.REVIEW: AMBER,
    FieldState.NA: MAROON,
}


def label(state: FieldState) -> str:
    return LABEL.get(state, str(state))


def chip(state: FieldState, text: str | None = None) -> str:
    c = COLOR.get(state, ACCENT)
    return (f"<span class='d2s-chip' style='color:{c};border-color:{c};"
            f"background:{c}14'>{text or label(state)}</span>")


# 표시원 배지 — 어느 수단이 이 칸을 불렀는가. 색으로 구분한다
FLAG_COLOR = {"확신도": ACCENT, "어휘": AMBER, "출처": MAROON}
_KIND = {"auto": GREEN, "review": AMBER, "na": MAROON, "off": "#8A97A1"}


def badge(text: str, kind: str = "off") -> str:
    """작은 배지 하나. 쪽 고르기의 날짜·개정표기 표시에 쓴다."""
    c = _KIND.get(kind, _KIND["off"])
    return (f"<span class='d2s-chip' style='color:{c};border-color:{c};"
            f"background:{c}14'>{text}</span>")


def flag_badge(source: str) -> str:
    """표시원 배지 — 확신도 · 어휘 · 출처."""
    c = FLAG_COLOR.get(source, ACCENT)
    return (f"<span class='d2s-chip' style='color:{c};border-color:{c};"
            f"background:{c}14'>{source}</span>")


# 사람의 조치를 화면에 영어 열거값으로 내보내지 않는다
ACTION = {"approve": "확인", "override": "수정", "na_confirm": "N/A 확인"}

# 안전·식별 표시 — 원시 열거값(safety/identity) 대신 짧은 한글로
SAFETY_TAG = {
    "safety": f"<span class='d2s-code' style='color:{MAROON}'> 안전</span>",
    "identity": f"<span class='d2s-code' style='color:{ACCENT}'> 식별</span>",
}


def chip_counts(counts: dict[str, int]) -> str:
    out = []
    for s in (FieldState.AUTO, FieldState.REVIEW, FieldState.NA):
        n = counts.get(s.value, 0)
        out.append(chip(s, f"{label(s)} {n}"))
    return " ".join(out)


def inject_css() -> None:
    """가시성 우선. **버튼을 전역으로 줄이지 않는다.**

    표를 조밀하게 하려고 `.stButton>button` 을 전역으로 줄였더니 쪽 고르기의
    주 동작 버튼까지 작아졌다(2026-08-27 사용자 지적). 조밀함은 표 안에서만
    필요하고, 화면 전체에 걸면 읽기가 나빠진다.
    """
    st.markdown("""
<style>
  .d2s-chip{display:inline-block;font-family:ui-monospace,Menlo,monospace;font-size:12.5px;
    font-weight:600;padding:2px 9px;border-radius:3px;border:1px solid;white-space:nowrap}
  .d2s-key{font-size:14.5px;font-weight:600;line-height:1.4}
  .d2s-code{font-family:ui-monospace,Menlo,monospace;font-size:11.5px;opacity:.72}
  .d2s-val{font-family:ui-monospace,Menlo,monospace;font-size:14.5px;line-height:1.4;
    word-break:break-all}
  .d2s-raw{font-size:12.5px;opacity:.78;line-height:1.5}
  .d2s-was{opacity:.62;text-decoration:line-through .5px}
  .d2s-note{font-size:12.5px;color:#8A5A12;line-height:1.5}
  .d2s-note1{font-size:12.5px;color:#8A5A12;line-height:1.45;white-space:nowrap;
    overflow:hidden;text-overflow:ellipsis}
  .d2s-req{font-family:ui-monospace,Menlo,monospace;font-size:12px;opacity:.75}
  .d2s-head{font-family:ui-monospace,Menlo,monospace;font-size:11.5px;letter-spacing:.06em;
    text-transform:uppercase;opacity:.7;padding-bottom:3px;font-weight:600}
  .d2s-row{border-bottom:1px solid rgba(128,128,128,.3);padding:2px 0 8px}
  .d2s-guide{font-size:13.5px;line-height:1.7;white-space:pre-wrap;
    border-left:3px solid #1F4E6B;padding:2px 0 2px 10px;margin:2px 0 6px}
  .d2s-legend{font-size:12.5px;opacity:.85;line-height:1.8}
  .d2s-legend i{display:inline-block;width:15px;height:10px;border-radius:2px;margin-right:6px}
  .d2s-sub{font-size:12.5px;opacity:.7;margin-top:-8px}

  /* 쪽 고르기 격자 — 체크박스를 이미지보다 먼저, 크게 */
  div[data-testid="stCheckbox"] label p{font-size:14px;font-weight:600}

  /* 표 안 버튼만 조밀하게. 전역에 걸지 않는다 */
  div[data-testid="column"] div[data-testid="stPopover"] button{
    padding:0.2rem 0.6rem;font-size:12.5px;min-height:0}
</style>""", unsafe_allow_html=True)


LEGEND = f"""<div class='d2s-legend'>
<span><i style='border:1.5px solid {GREEN};background:{GREEN}20'></i>
정상추출 — 여기서 읽었고 확신함</span><br>
<span><i style='border:1.5px dashed {AMBER};background:{AMBER}2b'></i>
확인필요 — 여기 같은데 확신 없음</span></div>"""
