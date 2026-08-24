# -*- coding: utf-8 -*-
"""집계 축 — 무엇끼리 묶어서 점수를 내는가

    from eval.groups import equipment_class, expected_absent

    equipment_class({"type_name": "Direct Operated Regulator"})  → "regulator"
    equipment_class({"type_name": "Flow Control Valve"})          → "control_valve"

왜 나누는가 (2026-08-24 결정)
─────────────────────────────────────────────────────────────
레귤레이터(자기구동 감압변)는 컨트롤밸브와 **다른 설비**다. 액추에이터가
없으므로 필드가 구조적으로 존재하지 않는다. 골든셋 d009(22PCV013, Fisher 627,
`Direct Operated Regulator`) 실측:

    28필드 중 12개가 N/A
    MVP 9필드 중 2개가 N/A — RATED CV, 그리고 **안전 필드인 FAIL ACTION**

그래서 두 가지를 하지 않는다.

① **범위에서 빼지 않는다.** 판별자가 태그가 아니기 때문이다. 실측상 PCV 중
   레귤레이터는 53%(20/38)이고 나머지 절반은 실제 컨트롤밸브다. PCV 를 빼면
   정상 컨트롤밸브 18건 이상이 함께 날아가고, FV 에 섞인 레귤레이터 2건은
   그대로 남는다. 태그로 자르면 양쪽으로 틀린다.

   그리고 레귤레이터는 이 과제의 핵심 주장("모르면 만들지 않는다")을 증명할
   **최고의 표본**이다 — 필드가 구조적으로 없으니까. 빼면 증명할 근거를 버린다.

② **한 덩어리로 집계하지 않는다.** 레귤레이터의 12개 N/A 는 정답이므로 맞히면
   만점이다(`compare.same("NA","NA")` → True). 그런데 그것을 컨트롤밸브와 섞어
   평균하면 두 가지가 왜곡된다.
       - 전체 정확도가 부풀려진다 (N/A 는 쉬운 정답이다)
       - 안전 필드 정확도가 희석된다 (레귤레이터에는 그 필드가 없다)

   → 결과 표를 `control_valve` / `regulator` 로 나누고, 안전 필드 정확도는
     컨트롤밸브만으로 낸다.

판별자는 이미 골든셋에 있다 — `TYPE NAME` 이다. d009 는 `Direct Operated
Regulator` 로 적혀 있다. 새 칼럼을 만들지 않는다.
"""
from __future__ import annotations

import re

CONTROL_VALVE = "control_valve"
REGULATOR = "regulator"
UNKNOWN = "unknown"

# `TYPE NAME` 에서 레귤레이터를 가리키는 표기.
# 에어셋 부속품(`Filter/Regulator`·`Air Regulator`)과 Fisher 양식 상용구
# (`Valve/Regulator Sizing Calculation`)를 태그로 오인한 전례가 있어 제외한다.
_NOISE = re.compile(
    r"FILTER\s*/?\s*REGULATOR|AIR\s*REGULATOR|REGULATOR\s*/?\s*FILTER"
    r"|VALVE\s*/\s*REGULATOR\s*SIZING|6\d[A-Z]{2,3}R", re.I)
_REG = re.compile(
    r"\bREGULATOR\b|DIRECT[\s-]*OPERATED|SELF[\s-]*OPERATED"
    r"|PRESSURE\s*REDUCING|감압", re.I)

# 레귤레이터에는 구조적으로 없는 필드. 여기가 N/A 인 것은 실패가 아니다.
#
# d009(Fisher 627 Direct Operated Regulator) 실측으로 만들었다. 표본 1건이므로
# 골든셋이 채워지면 다시 볼 것 — 레귤레이터 표본이 늘면 교집합으로 좁힌다.
REGULATOR_ABSENT = (
    "rated_cv",                # 레귤레이터는 Sizing Coefficient 로 적는다
    "actuator_fail_action",    # 액추에이터가 없다 (자기구동)
    "valve_body_type",
    "valve_leakage_class",
    "characteristic",
    "valve_plug_material",
    "valve_cage_material",
    "valve_stem_material",
    "positioner_manufacturer",
    "positioner_model_no",
)


def equipment_class(gold: dict) -> str:
    """골든셋 한 행의 설비 분류. `TYPE NAME` 으로 가른다.

    gold 는 `{field_key: 정답값}` 이다. 판단 근거가 없으면 UNKNOWN —
    추측하지 않는다(철학 4).
    """
    t = str(gold.get("type_name") or "")
    if not t.strip():
        return UNKNOWN
    if _REG.search(_NOISE.sub(" ", t)):
        return REGULATOR
    return CONTROL_VALVE


def expected_absent(cls: str) -> tuple[str, ...]:
    """이 분류에서 구조적으로 없는 필드. 여기가 N/A 인 것은 정답이다."""
    return REGULATOR_ABSENT if cls == REGULATOR else ()


def scoreable(cls: str, field_key: str) -> bool:
    """이 분류에서 이 필드를 점수에 넣을 수 있는가.

    안전 필드 정확도를 낼 때 레귤레이터를 분모에서 빼는 데 쓴다 —
    그 설비에는 애초에 그 필드가 없으므로 분모에 넣으면 숫자가 희석된다.
    """
    return field_key not in expected_absent(cls)
