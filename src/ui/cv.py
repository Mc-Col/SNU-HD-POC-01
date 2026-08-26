# -*- coding: utf-8 -*-
"""[Cv 계산] — N/A 를 값으로 바꿀 수 있는 유일한 수단.

Cv 물리 교차검증은 루프에서 뺐다(잘못된 제약이 정상 값을 리뷰필요로 밀어낸다).
대신 사람이 누르는 버튼으로 남겼다. 결과는 자동확정이 아니라 사람의 입력이다.

차압(ΔP)은 기준정보 항목이 아니다 (2026-08-23 결정 — 30필드에 넣지 않음).
따라서 문서에서 뽑을 수 없고 계산 시 사람이 넣는다. 화면이 그걸 숨기지 않는다.

채우는 대상은 REQUIRED CV 다 — 운전조건에서 필요한 Cv 이기 때문이다.
RATED CV(밸브 정격)는 밸브의 사이즈·트림에서 정해지므로 계산으로 채우지 않는다.
"""
from __future__ import annotations

import math
import re

import streamlit as st

from src.contracts import FieldRecord
from src.ui import session
from src.ui.source import UiDoc

# 공정조건 원천 필드 — 값이 아니라 '어디서 왔는지'를 같이 보여주기 위한 목록
Q_KEYS = ("normal_flow_rate",)
SG_KEYS = ("specific_gravity",)
P_KEYS = ("normal_pressure",)   # maximum_pressure 는 스키마에서 사라졌다

Q_UNITS = {"m3/h": 1.0, "gpm": 0.2271247}
P_UNITS = {"bar": 1.0, "kg/cm2": 0.980665, "psi": 0.0689476, "MPa": 10.0}

KV_TO_CV = 1.156


def first_number(text: str | None) -> float | None:
    """'12.5 / 8.2 kg/cm2G' 에서 12.5 를 집는다. 단위 변환은 MVP 범위 외."""
    if not text:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(text).replace(",", ""))
    return float(m.group()) if m else None


def cv_liquid(q_m3h: float, sg: float, dp_bar: float) -> tuple[float, float]:
    """액체 기준. Kv = Q·√(SG/ΔP), Cv = 1.156·Kv. (Q m3/h, ΔP bar)"""
    if q_m3h <= 0 or sg <= 0 or dp_bar <= 0:
        raise ValueError("유량·비중·차압은 모두 0 보다 커야 합니다")
    kv = q_m3h * math.sqrt(sg / dp_bar)
    return kv, kv * KV_TO_CV


def _source_line(d: UiDoc, keys: tuple[str, ...]) -> tuple[float | None, str]:
    for k in keys:
        r = d.record(k)
        if r and r.final_value:
            return first_number(r.final_value), f"{r.field_name} = {r.final_value}"
    return None, "문서에서 추출되지 않음"


def panel(d: UiDoc, rec: FieldRecord) -> None:
    """rec(정격 Cv) 를 채우기 위한 계산 패널. 적용하면 사람의 입력으로 기록된다."""
    q0, q_src = _source_line(d, Q_KEYS)
    sg0, sg_src = _source_line(d, SG_KEYS)
    _p0, p_src = _source_line(d, P_KEYS)

    st.caption("운전조건에서 필요한 Cv 를 계산합니다. 밸브 정격(RATED CV)이 아닙니다.")
    st.caption("문서에서 온 공정조건 — 단위 정규화는 MVP 범위 외이므로 원문 그대로입니다")
    st.markdown(
        f"<div class='d2s-raw'>유량 · {q_src}<br>비중 · {sg_src}<br>"
        f"입구압력 · {p_src}</div>", unsafe_allow_html=True)
    st.warning("차압(ΔP)은 기준정보 항목이 아니어서 문서에서 뽑을 수 없습니다. "
               "여기서 직접 입력합니다 — 입력값과 계산식은 변환 이력에 남습니다.", icon="⚠️")

    c1, c2 = st.columns([2, 1])
    q = c1.number_input("유량 Q", value=float(q0 or 0.0), min_value=0.0, step=1.0,
                        key=f"cv_q_{rec.field_key}")
    qu = c2.selectbox("단위", list(Q_UNITS), key=f"cv_qu_{rec.field_key}")

    c1, c2 = st.columns([2, 1])
    dp = c1.number_input("차압 ΔP (사람 입력)", value=0.0, min_value=0.0, step=0.1,
                         key=f"cv_dp_{rec.field_key}")
    pu = c2.selectbox("단위 ", list(P_UNITS), key=f"cv_pu_{rec.field_key}")

    sg = st.number_input("비중 SG", value=float(sg0 or 1.0), min_value=0.0, step=0.01,
                         key=f"cv_sg_{rec.field_key}")

    try:
        kv, cv = cv_liquid(q * Q_UNITS[qu], sg, dp * P_UNITS[pu])
    except ValueError as e:
        st.info(f"입력이 더 필요합니다 — {e}")
        return

    st.metric("계산된 Cv", f"{cv:.1f}", help=f"Kv {kv:.1f} × {KV_TO_CV}")
    trace = (f"[사람] Cv 계산 — Kv=Q·√(SG/ΔP), Q={q}{qu}, SG={sg}, ΔP={dp}{pu} "
             f"→ Kv {kv:.2f} → Cv {cv:.2f}")
    st.markdown(f"<div class='d2s-raw'>{trace}</div>", unsafe_allow_html=True)

    if st.button("이 값을 적용", key=f"cv_apply_{rec.field_key}", type="primary"):
        rec.transform_trace = list(rec.transform_trace) + [trace]
        rec.note = (rec.note + " | 사람이 공정조건으로 계산해 입력").strip(" |")
        session.apply_human(rec, "override", f"{cv:.1f}")
        st.rerun()
