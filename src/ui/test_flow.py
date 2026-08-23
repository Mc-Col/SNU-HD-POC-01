# -*- coding: utf-8 -*-
"""화면 자기검증 — 파서가 없어도 흐름과 잠금이 맞는지 확인한다.

    python -m pytest src/ui/test_flow.py -q

여기서 검증하는 것은 '화면이 계약을 따르는가' 하나다.
필수 필드가 남아 있으면 승인이 막히는가, 안전·식별 필드가 정상추출이어도
사람 확인을 요구하는가, 필수여부가 fields.yaml 에서 오는가.
"""
from __future__ import annotations

import io
import os

import pytest
from streamlit.testing.v1 import AppTest

from src import schema
from src.contracts import FieldState
from src.hooks import hooks
from src.ui import export, screens, source

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
APP = os.path.join(ROOT, "app.py")

screens.STEP_DELAY = 0.0          # 더미 지연은 테스트에서 뺀다


def _to_hitl() -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.button(key="btn_fixture").click().run()      # 1 → 3 확인화면
    at.button(key="btn_start").click().run()        # 4 추출 → 5 HITL
    assert at.session_state["stage"] == "hitl"
    return at


# ── 픽스처가 스키마를 이긴다는 착각을 막는다 ──────────────────

def test_meta_comes_from_schema_not_fixture():
    d = source.from_fixture()
    for r in d.records:
        f = schema.get(r.field_key)
        assert r.required == f.required
        assert r.safety == f.safety
        assert r.threshold == f.threshold
        assert r.field_name == f.name


def test_na_records_hold_no_value():
    for r in source.from_fixture().records:
        if r.state is FieldState.NA:
            assert r.value in (None, "", "N/A")
            assert r.note.strip(), "N/A 인데 비고가 없다 — 계약 위반"


# ── 잠금 ──────────────────────────────────────────────────────

def test_submit_locked_until_required_resolved():
    at = _to_hitl()
    d = at.session_state["doc"]

    left = {r.field_key for r in d.unresolved_required}
    # 확신도 미달(REVIEW) · 근거 없음(NA) · 안전·식별 필드(AUTO 이지만 확인 필요)
    assert left == {"engineering_tag_no", "valve_body_material",
                    "actuator_fail_action", "positioner_model_no"}, left
    assert at.button(key="btn_approve").disabled is True

    # 안전·식별 필드는 정상추출이어도 확인 버튼이 나온다
    at.button(key="ok_engineering_tag_no").click().run()
    at.button(key="ok_actuator_fail_action").click().run()
    assert at.button(key="btn_approve").disabled is True

    # 확인필요 → 사람이 값을 고친다
    at.text_input(key="in_valve_body_material").input("WCB").run()
    at.button(key="go_valve_body_material").click().run()

    # N/A → 근거 없음을 사람이 확인한다
    at.button(key="go_positioner_model_no").click().run()

    d = at.session_state["doc"]
    assert d.unresolved_required == []
    assert d.result.approvable is True
    assert at.button(key="btn_approve").disabled is False

    at.button(key="btn_approve").click().run()
    assert at.session_state["stage"] == "done"


def test_human_edit_reaches_excel():
    at = _to_hitl()
    at.text_input(key="in_valve_body_material").input("WCB").run()
    at.button(key="go_valve_body_material").click().run()

    d = at.session_state["doc"]
    rec = d.record("valve_body_material")
    assert rec.human_action == "override"
    assert rec.final_value == "WCB"          # 사람이 고쳤으면 그 값이 최종

    import io

    import openpyxl
    ws = openpyxl.load_workbook(io.BytesIO(export.build(d)))["Output"]
    assert ws.max_column == len(schema.all_fields()) + 1     # A열 + 30필드
    col = next(j for j in range(2, ws.max_column + 1)
               if ws.cell(2, j).value == schema.get("valve_body_material").db_code)
    assert ws.cell(7, col).value == "WCB"                    # 7행 = 도출 값


# ── 일괄 패스 ─────────────────────────────────────────────────

def test_bulk_approve_skips_safety_fields():
    at = _to_hitl()
    at.button(key="btn_bulk").click().run()
    d = at.session_state["doc"]

    for r in d.records:
        if r.state is FieldState.AUTO and r.safety == "normal":
            assert r.human_action == "approve"
        elif r.safety != "normal":
            assert r.human_action is None, "안전·식별 필드가 일괄 승인에 휩쓸렸다"
    assert at.button(key="btn_approve").disabled is True


# ── 근거 ──────────────────────────────────────────────────────

def test_bbox_overlay_renders_only_evidenced_fields():
    from src.ui import overlay
    d = source.from_fixture()
    boxes = overlay.boxes_for(d.records, 1, "valve_body_material")

    keyed = {r.field_key for r in d.records if r.bbox}
    assert len(boxes) == len(keyed) > 0
    assert sum(1 for b in boxes if b[6]) == 1          # 선택된 박스는 하나
    png = overlay.render(d.page_path, 1, boxes)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"

    for r in d.records:
        if r.state is FieldState.NA:
            assert r.bbox is None, "근거 없음인데 지면 위치가 있다"


# ── 확정된 결정이 코드가 아니라 정의서에 있는가 (2026-08-23) ───

def test_rated_cv_max_required_and_in_mvp():
    """Rated CV 는 필수 — 없는 데이터시트는 없다는 도메인 판단."""
    f = schema.get("rated_cv_max")
    assert f.required and f.mvp
    # 계산으로 채우는 값은 운전조건 기준이므로 NORMAL 이고, 그쪽은 선택이다
    assert not schema.get("rated_cv_normal").required


def test_actuator_type_is_not_an_extraction_target():
    """구동부는 FAIL POSITION 만. 따라서 자동 교차검증이 없다."""
    assert not schema.get("actuator_type").mvp
    assert schema.domain_rule("actuator_fail_action")["cross_check"] == []


def test_positioner_stays_required_and_is_resolved_by_human():
    """포지셔너 부재는 규칙으로 필수를 풀지 않고 사람이 N/A 확인한다."""
    assert schema.get("positioner_model_no").required
    assert schema.get("positioner_type").required

    at = _to_hitl()
    rec = at.session_state["doc"].record("positioner_model_no")
    assert rec.state is FieldState.NA and not rec.resolved

    at.button(key="go_positioner_model_no").click().run()
    rec = at.session_state["doc"].record("positioner_model_no")
    assert rec.human_action == "na_confirm" and rec.resolved


# ── 자연어 지침 ───────────────────────────────────────────────

@pytest.fixture
def guidance_restored():
    """지침 파일을 건드리는 시험은 원래 내용을 돌려놓는다."""
    orig = io.open(schema.GUIDANCE_PATH, encoding="utf-8").read()
    yield
    io.open(schema.GUIDANCE_PATH, "w", encoding="utf-8", newline="\n").write(orig)
    schema.reload()


def test_guidance_comes_from_yaml():
    g = schema.guidance("positioner_model_no")
    assert g and "N/A" in g["text"]
    assert schema.general_guidance()["text"].strip()
    assert "schema/guidance.yaml" in schema.config_hashes()


def test_guidance_can_be_written_from_the_screen(guidance_restored):
    at = _to_hitl()
    key = "valve_body_material"
    text = "바디와 트림이 한 칸에 있으면 앞이 바디다. (시험 문구)"

    at.text_area(key=f"gd_{key}").input(text).run()
    at.button(key=f"gdsave_{key}").click().run()

    schema.reload()
    assert text in schema.guidance(key)["text"]
    assert hooks.counters["on_rule_edit"] >= 1        # Loop C 입력이 남는다


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
