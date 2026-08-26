# -*- coding: utf-8 -*-
"""③ 두 경로 대조 검증.

VLM 쪽 입력은 **실제 mock 응답 파일**(`fixtures/vlm/mock_responses/`)을 쓴다.
직접 만든 자료로만 검증하면 계약이 어긋난 것을 못 잡는다.
"""
import json
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.contracts import RawExtraction                       # noqa: E402
from src.parsers.text.crosscheck import (AGREE, CONFLICT,     # noqa: E402
                                         NEITHER, NOTATION, TEXT_ONLY, VLM_ONLY,
                                         agreement_rate, crosscheck,
                                         numeric_flag, summary)

MOCK = os.path.join(ROOT, "fixtures", "vlm", "mock_responses", "clean_extraction.json")


@pytest.fixture(scope="module")
def vlm_mock() -> dict[str, str]:
    """VLM 파서가 낼 형태 그대로 읽는다."""
    with open(MOCK, encoding="utf-8") as f:
        doc = json.load(f)
    return {e["field_key"]: e["raw_value"] for e in doc["extractions"]
            if e.get("raw_value")}


def test_실제_mock_응답을_그대로_받는다(vlm_mock):
    assert vlm_mock["model_no"] == "ED-667"
    # 텍스트가 같은 값을 읽었다면 전부 일치여야 한다
    got = crosscheck(vlm_mock, dict(vlm_mock))
    assert {r.state for r in got} == {AGREE}
    assert agreement_rate(got) == 1.0


def test_다섯_가지_판정을_가른다(vlm_mock):
    text = dict(vlm_mock)
    text["model_no"] = "880"                    # 다른 값 — 실제 d010 에서 났던 오답
    text.pop("manufacturer")                    # 텍스트가 못 읽음 (로고에만 있는 값)
    text["required_cv"] = "126"                 # 양쪽 다 있음 (mock 에도 있다)
    text["viscosity"] = "1.7"                   # 텍스트만
    by = {r.field_key: r for r in crosscheck(vlm_mock, text)}
    assert by["model_no"].state == CONFLICT
    assert by["manufacturer"].state == VLM_ONLY
    assert by["viscosity"].state == TEXT_ONLY
    assert by["engineering_tag_no"].state == AGREE
    # 요청한 필드가 양쪽에 없으면 '없음'
    assert crosscheck({}, {}, fields=["rated_cv"])[0].state == NEITHER


def test_사람은_불일치에만_부른다(vlm_mock):
    text = dict(vlm_mock)
    text["model_no"] = "880"                    # 불일치
    text.pop("manufacturer")                    # 한쪽만 — 갈등이 아니다
    got = {r.field_key: r for r in crosscheck(vlm_mock, text)}
    assert got["model_no"].needs_human
    assert not got["manufacturer"].needs_human
    assert not got["engineering_tag_no"].needs_human


def test_단위와_로마자_차이는_일치로_본다():
    """비교를 다시 만들지 않고 eval/compare 를 쓰는지 확인한다."""
    got = {r.field_key: r.state for r in crosscheck(
        {"normal_flow_rate": "142.6", "valve_leakage_class": "CLASS IV"},
        {"normal_flow_rate": "142.6 m3/Hr", "valve_leakage_class": "Class 4"})}
    assert got == {"normal_flow_rate": AGREE, "valve_leakage_class": AGREE}


def test_표기_매핑_사전을_적용한다():
    """문서 원문(`CLOSE`)과 표준값(`FAIL CLOSE`)을 다른 값으로 부르지 않는다."""
    got = crosscheck({"actuator_fail_action": "CLOSE"},
                     {"actuator_fail_action": "FAIL CLOSE"})
    assert got[0].state == AGREE


def test_숫자는_같고_표기만_다르면_사람을_부르지_않는다():
    """사전에 없는 표기라도 숫자가 같으면 사람을 부르지 않는다(NOTATION).

    `300#` vs `ANSI CLASS 300` 은 2026-08-26 에 표기 매핑이 들어와 **일치**가 됐다.
    이 판정은 아직 사전에 없는 표기를 위해 남아 있다 — 사전이 자라면 줄어든다.
    """
    got = crosscheck({"valve_body_rating": "750#"},
                     {"valve_body_rating": "CL 750"})       # 사전에 없는 등급
    assert got[0].state == NOTATION
    assert not got[0].needs_human
    assert "표준형 미정" in got[0].as_note()


def test_합의율_분모는_두_경로가_다_읽은_칸이다():
    """한쪽만 읽은 칸을 분모에 넣으면 텍스트가 약한 문서에서 비율이 흔들린다."""
    got = crosscheck({"rated_cv": "236", "manufacturer": "FISHER"},
                     {"rated_cv": "236", "required_cv": "126"})
    assert summary(got) == {AGREE: 1, NOTATION: 0, CONFLICT: 0,
                            VLM_ONLY: 1, TEXT_ONLY: 1, NEITHER: 0}
    assert agreement_rate(got) == 1.0                     # 대조 가능한 칸은 1개, 일치
    assert agreement_rate([]) is None


def test_파이프라인이_주는_형태를_받는다():
    """계약은 RawExtraction 목록이다. dict 도 받지만 목록이 본래 입력이다."""
    vlm = [RawExtraction(field_key="rated_cv", raw_value="236"),
           RawExtraction(field_key="model_no", raw_value=None),          # 못 읽음
           RawExtraction(field_key="required_cv", raw_value="N/A")]      # 항목 없음
    text = [RawExtraction(field_key="rated_cv", raw_value="236.0"),
            RawExtraction(field_key="model_no", raw_value="667-ED")]
    by = {r.field_key: r for r in crosscheck(vlm, text)}
    assert by["rated_cv"].state == AGREE
    assert by["model_no"].state == TEXT_ONLY          # VLM 이 놓친 것을 후보로
    assert by["required_cv"].state == VLM_ONLY or by["required_cv"].state == NEITHER


def test_N_A_는_값으로_보지_않는다():
    got = crosscheck({"rated_cv": "N/A"}, {"rated_cv": "판독불가"})
    assert got[0].state == NEITHER


def test_같은_입력이면_같은_출력이다(vlm_mock):
    text = dict(vlm_mock); text["model_no"] = "880"
    a = [(r.field_key, r.state) for r in crosscheck(vlm_mock, text)]
    b = [(r.field_key, r.state) for r in crosscheck(vlm_mock, text)]
    assert a == b


def test_숫자_필드_표시는_rules_yaml_에서_읽는다():
    """채점기·평가 하네스와 같은 자리를 본다 (구현이 갈라지지 않게)."""
    assert numeric_flag("normal_flow_rate") is True
    assert numeric_flag("valve_body_material") is None


# ── 표기 통일 사전 (2026-08-26) ─────────────────────────────────


def test_같은_등급의_다른_표기를_접는다():
    """`600#` · `ANSI CLASS 600` · `ASME CL.600` 은 같은 등급이다.

    실측 — 골든셋 30건에서 틀린 61칸 중 27칸이 이런 표기 차이였다.
    """
    from src.parsers.text.crosscheck import standardize
    for v in ["600#", "ANSI CLASS 600", "ASME CL.600", "CL 600", "600"]:
        assert standardize("valve_body_rating", v) == "CLASS 600", v


def test_등급이_아닌_값은_건드리지_않는다():
    """`MOP 150 PSIG` 는 댐퍼 최대 운전압력이지 ANSI 등급이 아니다."""
    from src.parsers.text.crosscheck import standardize
    assert standardize("valve_body_rating", "MOP 150 PSIG") == "MOP 150 PSIG"


def test_같은_회사의_다른_이름을_접는다():
    from src.parsers.text.crosscheck import standardize
    for v in ["FISHER", "Fisher Controls", "Nippon Fisher Co.,Ltd."]:
        assert standardize("manufacturer", v) == "FISHER", v
    # 포지셔너 제조사도 같은 사전을 쓴다 (같은 회사가 둘 다 만든다)
    assert standardize("positioner_manufacturer", "Fisher Controls") == "FISHER"


def test_누설등급은_규격대로_로마자로_통일한다():
    """ANSI/FCI 70-2 가 로마자를 쓴다."""
    from src.parsers.text.crosscheck import standardize
    for v in ["CLASS 4", "ANSI Class IV", "Class IV"]:
        assert standardize("valve_leakage_class", v) == "CLASS IV", v


def test_사이즈는_표기만_접고_단위는_바꾸지_않는다():
    """`unit_conversion: enabled: false` — 인치↔mm 변환은 MVP 범위 밖이다."""
    from src.parsers.text.crosscheck import standardize
    assert standardize("valve_body_size", "NPS 2") == '2"'
    assert standardize("valve_body_size", '1" (25A)') == '1"'
    assert standardize("valve_body_size", "IN DIA. 200 mm") == "IN DIA. 200 mm"


def test_사전을_거쳐야_같아지는_값은_일치로_본다():
    """두 경로가 `600#` 과 `ANSI CLASS 600` 을 냈다면 사람을 부를 일이 아니다."""
    got = crosscheck({"valve_body_rating": "600#"},
                     {"valve_body_rating": "ANSI CLASS 600"})
    assert got[0].state == AGREE
