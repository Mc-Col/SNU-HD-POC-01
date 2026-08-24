# -*- coding: utf-8 -*-
"""fixtures/format/ 으로 자기 검증한다."""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.contracts import FailureKind                                  # noqa: E402
from src.validate.format import load_format_rules, validate_format     # noqa: E402

FIXTURE = os.path.join(ROOT, "fixtures", "format", "case_basic.json")


@pytest.fixture(scope="module")
def case() -> dict:
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def _run(case: dict, name: str):
    block = case[name]
    got = validate_format(block["values"], rules=case["rules"])
    return [{"field_key": v.field_key, "rule": v.rule, "value": v.value} for v in got], block["expected"]


def test_위반을_빠짐없이_같은_순서로_낸다(case):
    got, expected = _run(case, "위반_케이스")
    assert got == expected


def test_정상_입력에는_위반이_없다(case):
    got, expected = _run(case, "정상_케이스")
    assert got == expected


def test_모든_위반은_FORMAT_으로_표시된다(case):
    for v in validate_format(case["위반_케이스"]["values"], rules=case["rules"]):
        assert v.kind is FailureKind.FORMAT
        assert v.reason.strip()                      # 사유 없는 위반은 없다


def test_NA_와_공백은_값_없음으로_본다(case):
    keys = {v.field_key for v in validate_format(case["위반_케이스"]["values"], rules=case["rules"])}
    assert "valve_plug_material" in keys             # "N/A"
    assert "valve_body_material" in keys             # "  "


def test_계약에_없는_키를_삼키지_않는다(case):
    got = validate_format(case["위반_케이스"]["values"], rules=case["rules"])
    assert any(v.rule == "unknown_field" and v.field_key == "spring_range" for v in got)


def test_규칙은_코드가_아니라_yaml_에서_읽는다():
    """format_rules 절이 아직 없으면 형식 검사는 하지 않는다 (하드코딩 금지)."""
    rules = load_format_rules()
    assert isinstance(rules, dict)
    got = validate_format({"rated_cv_max": "약 53.8"})
    assert not any(v.rule == "numeric" for v in got)


def test_같은_입력이면_같은_출력이다(case):
    vals, rules = case["위반_케이스"]["values"], case["rules"]
    a = [(v.field_key, v.rule) for v in validate_format(vals, rules=rules)]
    b = [(v.field_key, v.rule) for v in validate_format(vals, rules=rules)]
    assert a == b
