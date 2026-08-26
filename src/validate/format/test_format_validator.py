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
from src.validate.format import (check_value, load_format_rules,       # noqa: E402
                                 validate_format)

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


# ── format_rules 가 실제로 도는 상태 (2026-08-25) ────────────────
#
# 그전까지 이 모듈은 규칙이 없어 사실상 꺼져 있었다. 아래는 규칙을 켜면서
# 지켜야 한다고 판단한 선들이다.


def test_단위_표기는_위반이_아니다():
    """옛 데이터시트는 단위를 사람이 손으로 썼다.

    `142.6 m3/Hr` · `205 kg/cm2(g)` 가 모두 정상이고, 글자가 깨진 `(m?h)` 도
    실물에 있다(19FV077). 단위를 위반으로 잡으면 값을 제대로 읽었는지가 아니라
    필사 습관을 재게 된다 — 숫자가 하나라도 있으면 통과다.
    """
    for v in ["142.6 m3/Hr", "205 kg/cm2(g)", "30 (m?h)", "49 ℃", "0 cP", "1-1/2in"]:
        assert not [x for x in check_value("normal_flow_rate", v) if x.rule == "numeric"], v


def test_숫자가_하나도_없으면_잡는다():
    """실제로 있었던 오답 — 밸브 등급 칸에 연결 형식이 들어왔다 (15LV015)."""
    bad = check_value("valve_body_rating", "RF FLANGED")
    assert [x.rule for x in bad] == ["numeric"]
    assert bad[0].kind is FailureKind.FORMAT


def test_태그_판정은_preprocess_에_맡긴다():
    """명명 규칙(Area-설비종류-일련번호)을 여기 다시 쓰지 않는다."""
    assert not check_value("engineering_tag_no", "11-FV-048")
    assert not check_value("engineering_tag_no", "B10-TV-040")     # Area 에 문자가 붙는 형태
    assert [x.rule for x in check_value("engineering_tag_no", "3582G")] == ["tag"]


def test_골든셋_정답값을_위반으로_잡지_않는다():
    """규칙이 너무 좁으면 정상 값이 위반이 된다 — 오탐이 없어야 쓸 수 있다.

    골든셋 20건의 정답값 462칸을 검사해 위반 0건임을 2026-08-25 에 확인했다.
    여기서는 실물에서 관측된 표기를 대표로 걸어 회귀를 막는다.
    """
    ok = {
        "engineering_tag_no": "10-FV-007A",
        "manufacturer": "VALSTONE CONTROLS, INC.",
        "model_no": "V100 SERIES",
        "normal_flow_rate": "142.6 m3/Hr",
        "normal_pressure": "205 kg/cm2(g)",
        "valve_body_rating": "ANSI CLASS 300",
        "valve_body_size": "IN DIA. 200 mm",
        "valve_leakage_class": "ANSI Class II",
        "valve_plug_material": "316 SST + STELLITE",
        "characteristic": "Modified Percentage",
        "actuator_fail_action": "Fail Close",
        "positioner_model_no": "3821-28EA-D41L-0130-00",
    }
    got = [v for v in validate_format(ok) if v.rule != "required" and v.field_key in ok]
    assert got == [], [(v.field_key, v.rule, v.value) for v in got]


def test_어댑터가_파이프라인_계약을_지킨다():
    from src import schema
    from src.contracts import RawExtraction
    from src.validate.format import FormatValidator

    fv = FormatValidator()
    found = RawExtraction(field_key="x", raw_value="있음")
    missing = RawExtraction(field_key="x", raw_value=None)

    assert fv.check(schema.get("valve_body_rating"), "ANSI CLASS 300", found, {})[0] is FailureKind.NONE
    kind, why = fv.check(schema.get("valve_body_rating"), "RF FLANGED", found, {})
    assert kind is FailureKind.FORMAT and why.strip()
    # 값이 없을 때는 형식 위반이 아니다 — 못 읽은 것과 비어 있는 것을 가른다
    assert fv.check(schema.get("rated_cv"), None, missing, {})[0] is FailureKind.NO_EVIDENCE
    assert fv.check(schema.get("rated_cv"), None, found, {})[0] is FailureKind.EXTRACTION
