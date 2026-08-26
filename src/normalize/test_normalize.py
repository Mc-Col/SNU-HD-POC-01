# -*- coding: utf-8 -*-
"""④ Normalize 자기 검증 — 도출 규칙과 안전장치."""
from __future__ import annotations

import pytest

from src import schema
from src.contracts import RawExtraction
from src.normalize import Normalizer


@pytest.fixture(scope="module")
def n():
    return Normalizer()


@pytest.fixture(scope="module")
def fields():
    return {f.key: f for f in schema.all_fields()}


def _empty(key: str) -> RawExtraction:
    """파서가 값을 못 낸 상태 — 도출이 발동하는 조건."""
    return RawExtraction(field_key=key, raw_value=None)


# ── 태그 → 밸브 종류 ────────────────────────────────────────────
@pytest.mark.parametrize("tag,expect", [
    ("10-FV-002", "Flow Control Valve"),
    ("10-HV-010", "Hand Valve"),
    ("15-LV-015", "Level Control Valve"),
    ("52-PV-014", "Pressure Control Valve"),
    ("10-PDV-067", "Pressure Differential Valve"),
    ("22-PCV-013", "Pressure Control Valve"),
])
def test_type_name_도출(n, fields, tag, expect):
    v, trace = n.run(_empty("type_name"), fields["type_name"],
                     {"engineering_tag_no": tag})
    assert v == expect
    assert any("문서 근거 없음" in t for t in trace), "도출 근거를 trace 에 남겨야 한다"


def test_태그가_없으면_도출하지_않는다(n, fields):
    v, trace = n.run(_empty("type_name"), fields["type_name"],
                     {"engineering_tag_no": ""})
    assert v is None
    assert any("도출 불가" in t for t in trace)


def test_모르는_설비종류는_도출하지_않는다(n, fields):
    v, _ = n.run(_empty("type_name"), fields["type_name"],
                 {"engineering_tag_no": "10-ZZ-001"})
    assert v is None, "매핑에 없는 종류를 억지로 채우면 안 된다"


# ── 유체명 → 상태 ──────────────────────────────────────────────
@pytest.mark.parametrize("fluid,expect", [
    ("SOUR WATER", "LIQUID"),
    ("VACUUM RESIDUE", "LIQUID"),
    ("KEROSENE", "LIQUID"),
    ("M.P. STEAM", "GAS"),
    ("S.H. STEAM", "GAS"),
    ("Fuel Gas", "GAS"),
    ("ATOMIZING STEAM", "GAS"),
])
def test_fluid_state_도출(n, fields, fluid, expect):
    v, _ = n.run(_empty("fluid_state"), fields["fluid_state"], {"fluid_name": fluid})
    assert v == expect


def test_낱말_경계를_지킨다(n, fields):
    """GASOLINE 이 GAS 로 잡히면 안 된다 — 부분 문자열 매칭 금지."""
    v, _ = n.run(_empty("fluid_state"), fields["fluid_state"], {"fluid_name": "GASOLINE"})
    assert v == "LIQUID"


# ── 안전장치 ───────────────────────────────────────────────────
def test_값이_있으면_도출하지_않는다(n, fields):
    """파서가 읽은 값이 우선이다. 도출이 그것을 덮어쓰면 안 된다."""
    ex = RawExtraction(field_key="type_name", raw_value="Hand Valve")
    v, _ = n.run(ex, fields["type_name"], {"engineering_tag_no": "10-FV-002"})
    assert v == "Hand Valve"


def test_context_없이도_동작한다(n, fields):
    """구형 호출(2인자) 호환 — 계약 확장 이전 코드가 깨지면 안 된다."""
    v, _ = n.run(_empty("type_name"), fields["type_name"])
    assert v is None


def test_도출_규칙이_없는_필드는_그대로_빈다(n, fields):
    v, _ = n.run(_empty("manufacturer"), fields["manufacturer"],
                 {"engineering_tag_no": "10-FV-002"})
    assert v is None, "규칙이 없는 필드에 값을 만들면 철학 4 위반이다"
