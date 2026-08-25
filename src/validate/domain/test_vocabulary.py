# -*- coding: utf-8 -*-
"""허용 어휘 검증 자체 검증.

**절대 통과해선 안 되는 것**을 같은 파일에 나란히 둔다(인사이트 49) —
관대함을 늘릴 때마다 이쪽이 깨지는지 본다.
"""
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import schema                                        # noqa: E402
from src.contracts import FailureKind                         # noqa: E402
from src.validate.domain import vocabulary as V               # noqa: E402


@pytest.fixture(autouse=True)
def _clean():
    V.reset()
    yield
    V.reset()


def _f(key):
    return schema.get(key)


# ── 판정 ────────────────────────────────────────────────────────

@pytest.mark.parametrize("key,value", [
    ("valve_leakage_class", "CLASS 4"),
    ("valve_leakage_class", "class 4"),        # 대소문자는 차이가 아니다
    ("characteristic", "EQUAL PERCENTAGE"),
    ("actuator_type", "DIAPHRAGM"),
    ("valve_body_type", "GLOBE"),
    ("manufacturer", "FISHER"),
    ("valve_body_material", "C5"),
    ("valve_body_material", "A216 WCB"),
])
def test_어휘_안이면_통과(key, value):
    kind, _ = V.check(_f(key), value)
    assert kind is FailureKind.NONE


@pytest.mark.parametrize("key,value", [
    ("valve_leakage_class", "CLASS 7"),
    ("characteristic", "PARABOLIC"),
    ("actuator_type", "HYDRAULIC"),
    ("valve_body_material", "CS"),             # 탄소강 — 이 코퍼스 어휘 밖
    ("valve_body_material", "CF-"),            # 미완성
    ("valve_body_material", "C.S"),
])
def test_어휘_밖이면_확인필요(key, value):
    kind, why = V.check(_f(key), value)
    assert kind is FailureKind.FORMAT
    assert "허용 어휘에 없는" in why


@pytest.mark.parametrize("key", [
    "model_no",                 # 열린 값
    "engineering_tag_no",       # 식별 — 일부러 제외했다
    "rated_cv",                 # 수치
    "equipment_full_description",
])
def test_어휘_없는_필드는_판정하지_않는다(key):
    kind, _ = V.check(_f(key), "무슨 값이든")
    assert kind is FailureKind.NONE


@pytest.mark.parametrize("value", [None, "", "   "])
def test_빈값은_판정하지_않는다(value):
    assert V.check(_f("valve_leakage_class"), value)[0] is FailureKind.NONE


# ── 절대 하면 안 되는 것 ────────────────────────────────────────

def test_어휘_밖_값을_고치지_않는다():
    """`CS` 를 `C5` 로 바꾸면 탄소강이 Cr-Mo 합금강이 된다."""
    kind, why = V.check(_f("valve_body_material"), "CS")
    assert kind is FailureKind.FORMAT
    assert "값을 바꾸지 않고" in why


def test_이웃이_붙은_어휘는_가까운_값을_알려주지_않는다():
    """`300#`·`600#`·`900#` 은 한 글자 차이다. 하나를 고르면 잘못된 유도다."""
    vocab = schema.allowed_values("valve_body_rating")
    assert V._nearest("?00#", vocab) is None


def test_안전_필드는_검증하되_보정하지_않는다():
    """보정에서 빼는 것과 검증에서 빼는 것은 다른 얘기다.

    검증은 값을 바꾸지 않으므로 안전 필드에서도 위험이 없다. 오히려 여기가
    가장 필요하다 — 실측에서 표시를 놓친 칸의 최대 항목이 이 필드였다.
    반대로 **보정 대상에 들어가면 안 된다.** FAIL OPEN 과 FAIL CLOSE 는
    어휘 안에서 서로 이웃이라 유사 보정이 가장 잘 걸리고, 틀리면 가장 비싸다.
    """
    assert schema.allowed_values("actuator_fail_action") ==         ("FAIL OPEN", "FAIL CLOSE")
    assert "actuator_fail_action" not in schema.enum_correct_fields()
    assert "actuator_fail_action" in schema.enum_flag_fields()
    # 어휘 밖은 표시되지만 값은 그대로다
    kind, why = V.check(_f("actuator_fail_action"), "R SUG (HD) -")
    assert kind is FailureKind.FORMAT and "값을 바꾸지 않고" in why


def test_식별_필드는_어휘가_없다():
    assert schema.allowed_values("engineering_tag_no") == ()


# ── 후보 큐 ─────────────────────────────────────────────────────

def test_어휘_밖만_큐에_쌓인다():
    V.observe("valve_body_material", "C5", "d001")      # 어휘 안 — 안 쌓임
    V.observe("valve_body_material", "CS", "d002")
    assert [c.value for c in V.candidates()] == ["CS"]


def test_같은_값은_합쳐서_센다():
    for d in ("d002", "d003", "d004"):
        V.observe("valve_body_material", "CS", d, label="Material")
    (c,) = V.candidates()
    assert c.count == 3 and c.docs == ["d002", "d003", "d004"]
    assert c.labels == ["Material"]


def test_표기가_달라도_같은_후보로_묶인다():
    V.observe("valve_body_material", "CS", "d002")
    V.observe("valve_body_material", "c.s", "d003")
    assert len(V.candidates()) == 1


def test_빈도순_정렬():
    for _ in range(3):
        V.observe("characteristic", "PARABOLIC", "d001")
    V.observe("actuator_type", "HYDRAULIC", "d002")
    assert [c.value for c in V.candidates()] == ["PARABOLIC", "HYDRAULIC"]


def test_최소_빈도로_걸러낸다():
    V.observe("characteristic", "PARABOLIC", "d001")
    assert V.candidates(min_count=2) == []


def test_행에_보정가능_여부가_들어간다():
    V.observe("characteristic", "PARABOLIC", "d001")     # 보정 대상 필드
    V.observe("valve_body_material", "CS", "d002")       # 검증 전용 필드
    rows = {r["field_key"]: r for r in V.as_rows()}
    assert rows["characteristic"]["correctable"] is True
    assert rows["valve_body_material"]["correctable"] is False


def test_요약():
    V.observe("characteristic", "PARABOLIC", "d001")
    V.observe("characteristic", "PARABOLIC", "d002")
    V.observe("valve_body_material", "CS", "d003")
    assert V.summary() == {"candidates": 2, "observations": 3, "fields": 2}


# ── 표준값이 합의와 같은가 ──────────────────────────────────────

def test_표준값_합의_확인():
    """2026-08-25 결정 — 누설등급은 아라비아, 특성은 철자."""
    assert schema.allowed_values("valve_leakage_class")[2] == "CLASS 4"
    assert "EQUAL PERCENTAGE" in schema.allowed_values("characteristic")
    assert "EQUAL PERCENT" not in schema.allowed_values("characteristic")
