# -*- coding: utf-8 -*-
"""집계 축 자체 검증 — 골든셋 실측 `TYPE NAME` 으로 만든다.

    python -m pytest eval/test_groups.py

이 파일이 지키는 것
    ① d009 는 레귤레이터, d001~d008·d010·d011 은 컨트롤밸브로 갈린다
    ② 에어셋 부속품·Fisher 양식 상용구를 레귤레이터로 오인하지 않는다
    ③ 판단 근거가 없으면 추측하지 않는다 (UNKNOWN)
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from eval.groups import (CONTROL_VALVE, REGULATOR, UNKNOWN,  # noqa: E402
                         equipment_class, expected_absent, scoreable)


# ── ① 골든셋 실측 TYPE NAME ─────────────────────────────────────
@pytest.mark.parametrize("type_name,want", [
    ("Direct Operated Regulator", REGULATOR),      # d009 22PCV013
    ("Flow Control Valve", CONTROL_VALVE),         # d001~d004, d008, d011
    ("Pressure Control Valve", CONTROL_VALVE),     # d006, d010
    ("Level Control Valve", CONTROL_VALVE),        # d007
    ("Self Operated Regulator", REGULATOR),
    ("Pressure Reducing Regulator", REGULATOR),
    ("감압변", REGULATOR),
])
def test_TYPE_NAME_으로_설비를_가른다(type_name, want):
    assert equipment_class({"type_name": type_name}) == want


# ── ② 오인하지 않는다 ──────────────────────────────────────────
@pytest.mark.parametrize("type_name", [
    "Control Valve with Filter/Regulator",   # 에어셋 부속품
    "Control Valve (Air Regulator 67CFR)",   # 실측 12FV020 문구
    "Valve/Regulator Sizing Calculation",    # Fisher 양식 상용구
])
def test_에어셋과_상용구를_레귤레이터로_보지_않는다(type_name):
    assert equipment_class({"type_name": type_name}) == CONTROL_VALVE


# ── ③ 근거가 없으면 추측하지 않는다 ────────────────────────────
@pytest.mark.parametrize("gold", [{}, {"type_name": ""}, {"type_name": None},
                                  {"type_name": "   "}])
def test_근거가_없으면_UNKNOWN(gold):
    assert equipment_class(gold) == UNKNOWN


# ── 구조적으로 없는 필드 ────────────────────────────────────────
def test_레귤레이터에_없는_필드():
    absent = expected_absent(REGULATOR)
    # d009 에서 실제로 N/A 였던 것들
    for k in ("rated_cv", "actuator_fail_action", "valve_body_type",
              "valve_leakage_class", "characteristic",
              "positioner_manufacturer", "positioner_model_no"):
        assert k in absent, k
    # 레귤레이터에도 있는 것 — 빠져 있어야 한다
    for k in ("engineering_tag_no", "manufacturer", "model_no",
              "valve_body_size", "valve_body_rating", "valve_body_material",
              "required_cv", "valve_seat_material"):
        assert k not in absent, k


def test_컨트롤밸브는_전부_점수에_들어간다():
    assert expected_absent(CONTROL_VALVE) == ()
    for k in ("rated_cv", "actuator_fail_action", "positioner_model_no"):
        assert scoreable(CONTROL_VALVE, k)


def test_안전필드는_레귤레이터_분모에서_빠진다():
    """레귤레이터에는 FAIL ACTION 이 없다 — 분모에 넣으면 숫자가 희석된다."""
    assert not scoreable(REGULATOR, "actuator_fail_action")
    assert scoreable(CONTROL_VALVE, "actuator_fail_action")
    # 식별 필드는 레귤레이터에도 있다
    assert scoreable(REGULATOR, "engineering_tag_no")


def test_UNKNOWN_은_아무것도_빼지_않는다():
    """분류를 모를 때 필드를 빼주면 조용히 점수가 오른다."""
    assert expected_absent(UNKNOWN) == ()
    assert scoreable(UNKNOWN, "actuator_fail_action")
