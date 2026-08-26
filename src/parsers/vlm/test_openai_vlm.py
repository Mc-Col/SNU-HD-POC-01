# -*- coding: utf-8 -*-
"""③-b VLM 파서 회귀 테스트 — mock 응답으로 계약 준수와 원문 보존을 검증한다.

실제 API 를 호출하지 않는다. 클라이언트는 전부 주입된 mock 이다.

여기서 고정하는 것은 **한 번 깨진 적이 있거나, 깨지면 조용히 틀리는** 동작들이다.
    · 원문 무변형 — 파서가 값·라벨을 손대면 하류가 정답을 낼 수 없다
    · 직접/역전 표기 보존 — 축약되면 안전 필드가 정반대로 확정된다
    · 체크박스 애매 시 미생성 — 추측이 오적재가 된다
    · 없음 vs 판독불가 구분 — 하류 처리가 다르다(N/A 확정 vs 재시도)
    · bbox 정규화 범위 — 틀린 좌표는 없는 것보다 나쁘다
    · 재판독 규정 문구 — '값이 틀렸다' 류는 환각을 유도한다
"""
from __future__ import annotations                      # 타입 표기 일관성 유지

import json                                             # 응답 조립

import pytest                                           # 테스트 프레임워크

from src import schema                                   # 필드 정의 (공용)
from src.contracts import ParserType, RawExtraction, Target, TriageResult
from src.parsers.vlm import MemoryResponseCache, VlmParser
from src.parsers.vlm.conftest import vlm_item, vlm_payload   # 응답 조립 보조
from src.parsers.vlm.openai_vlm import REREAD_SYSTEM, SYSTEM  # 프롬프트 직접 점검

# 이 모듈 테스트에서 반복해 쓰는 필드 (호출 1회, 검증 대상 명확)
FAIL_ACTION = "actuator_fail_action"
MANUFACTURER = "manufacturer"


def _fields(*keys):
    """field_key 로 공용 스키마의 Field 객체를 꺼낸다."""
    return [schema.get(k) for k in keys]


def _triage(page: int = 1) -> TriageResult:
    """지정 페이지를 target 으로 갖는 최소 TriageResult."""
    return TriageResult(
        source_path="x.tif",
        document_class=__import__("src.contracts", fromlist=["DocumentClass"]).DocumentClass.DATASHEET,
        targets=[Target(page_from=page, page_to=page)],
    )


@pytest.fixture
def parser(fake_openai, monkeypatch, tmp_path):
    """mock 클라이언트를 주입한 VlmParser 를 만드는 팩토리.

    페이지 렌더는 실제 파일이 필요하므로, 합성 이미지를 써서 렌더 단계를 대체한다.
    """
    def _make(*payloads, **kwargs):
        client = fake_openai(*payloads)
        p = VlmParser(client=client, render_dir=str(tmp_path), **kwargs)
        # 렌더를 fixture 이미지로 대체한다 — 실제 tif 를 두지 않는다(회사 문서 반출 금지).
        png = str(tmp_path / "page.png")
        from PIL import Image
        Image.new("L", (1240, 1753), color=255).save(png)
        monkeypatch.setattr(p, "_png", lambda path, page: png)
        return p, client
    return _make


# ══════════════════════════════════════════════════════════════════
#  ① 계약 준수
# ══════════════════════════════════════════════════════════════════

def test_returns_raw_extraction_per_requested_field(parser):
    """요청한 필드마다 정확히 하나의 RawExtraction 이 나온다."""
    fields = _fields(FAIL_ACTION, MANUFACTURER)
    p, client = parser(vlm_payload(**{
        FAIL_ACTION: vlm_item(raw_value="Close", raw_label="Air Fails Valve to"),
        MANUFACTURER: vlm_item(raw_value="FISHER", raw_label="(좌측 상단 로고)"),
    }))
    out = p.extract("x.tif", _triage(), fields)

    assert len(out) == 2                                    # 요청 수와 같다
    assert all(isinstance(r, RawExtraction) for r in out)   # 계약 타입
    assert all(r.parser is ParserType.VLM for r in out)     # 경로 표시
    assert client.call_count == 1                           # 필드별이 아니라 1회 일괄 호출


# ══════════════════════════════════════════════════════════════════
#  ② 원문 그대로 보존 — 가장 중요한 요구사항
# ══════════════════════════════════════════════════════════════════

def test_raw_value_and_label_are_not_normalized(parser):
    """파서는 값과 라벨을 어떤 방식으로도 변형하지 않는다."""
    tricky = "Air Fails Valve to : Open"                    # 구두점·공백이 섞인 원문
    p, _ = parser(vlm_payload(**{
        FAIL_ACTION: vlm_item(raw_value=tricky, raw_label="Air Fails Valve to"),
    }))
    rec = p.extract("x.tif", _triage(), _fields(FAIL_ACTION))[0]
    assert rec.raw_value == tricky                          # 문구 그대로
    assert rec.raw_label == "Air Fails Valve to"            # 라벨도 그대로


def test_direct_and_inverted_labels_survive_unchanged(parser):
    """직접 표기와 역전 표기가 축약되지 않고 구분 가능한 상태로 남는다.

    이것이 깨지면 안전 필드(ACTUATOR FAIL ACTION)가 정반대로 확정된다.
    'Air Fails Valve to : Open' → FAIL OPEN (직접)
    'Air-to-Open (ATO)'          → FAIL CLOSE (역전)
    두 문구는 'Fails Valve' 두 단어만 다르고 결과가 반대다.
    """
    direct = vlm_payload(**{FAIL_ACTION: vlm_item(
        raw_value="Open", raw_label="Air Fails Valve to",
        row_text="Air Fails Valve to   Lock [ ]   Open [X]   Close [ ]")})
    inverted = vlm_payload(**{FAIL_ACTION: vlm_item(
        raw_value="Opens", raw_label="Increase Signal Valve",
        row_text="Increase Signal Valve   [X] Opens        Closes [ ]")})

    p1, _ = parser(direct)
    rec1 = p1.extract("x.tif", _triage(), _fields(FAIL_ACTION))[0]
    p2, _ = parser(inverted)
    rec2 = p2.extract("x.tif", _triage(), _fields(FAIL_ACTION))[0]

    # 라벨로 계열을 구분할 수 있어야 한다 — 축약되면 구분이 불가능해진다.
    assert "Fails" in rec1.raw_label                        # 직접 표기의 표지
    assert "Fails" not in rec2.raw_label                    # 역전 표기에는 없다
    assert rec1.raw_label != rec2.raw_label                 # 서로 다른 라벨
    # 행 전체가 note 에 보존되어 하류가 재판단할 수 있다.
    assert "Lock [ ]" in rec1.note                          # 체크박스 행 원문
    assert "Opens" in rec2.note


def test_shared_cell_keeps_whole_cell_in_note(parser):
    """한 칸이 두 필드를 먹이면 칸 전체가 note 에 보존된다.

    'Size and Type' 칸의 '2", 667-EZ' 는 바디 사이즈와 모델번호를 동시에 담는다.
    하류(schema/rules.yaml 의 복합 라벨 규칙)가 이 칸을 분해하므로 원본이 남아야 한다.
    """
    whole = '2", 667-EZ'
    p, _ = parser(vlm_payload(
        valve_body_size=vlm_item(raw_value='2"', raw_label="Size and Type", row_text=whole),
        model_no=vlm_item(raw_value="667-EZ", raw_label="Size and Type", row_text=whole),
    ))
    out = p.extract("x.tif", _triage(), _fields("valve_body_size", "model_no"))
    assert [r.raw_value for r in out] == ['2"', "667-EZ"]    # 조각은 각 필드에
    for rec in out:
        assert whole in rec.note                             # 칸 전체가 보존된다


# ══════════════════════════════════════════════════════════════════
#  ③ 없음 vs 판독불가 — 하류 처리가 다르다
# ══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("reason,expected", [
    ("no_evidence", "근거 없음"),
    ("unreadable", "판독 실패"),
    ("checkbox_ambiguous", "체크박스"),
])
def test_absence_reason_is_distinguished_in_note(parser, reason, expected):
    """값이 없는 이유가 note 로 구분된다 (N/A 확정 vs 재시도 대상)."""
    p, _ = parser(vlm_payload(**{
        FAIL_ACTION: vlm_item(raw_value=None, absence_reason=reason),
    }))
    rec = p.extract("x.tif", _triage(), _fields(FAIL_ACTION))[0]
    assert rec.raw_value is None                            # 값은 없다
    assert expected in rec.note                             # 사유가 구분되어 남는다


def test_checkbox_ambiguous_never_guesses(parser):
    """체크박스가 애매하면 값을 만들어내지 않는다 (추측이 오적재가 된다)."""
    p, _ = parser(vlm_payload(**{
        FAIL_ACTION: vlm_item(raw_value=None, absence_reason="checkbox_ambiguous",
                              note="Open 과 Close 두 칸에 표시가 있음"),
    }))
    rec = p.extract("x.tif", _triage(), _fields(FAIL_ACTION))[0]
    assert rec.raw_value is None                            # 추측 금지
    assert "두 칸에 표시" in rec.note                         # 무엇을 봤는지 남는다


# ══════════════════════════════════════════════════════════════════
#  ④ bbox — 틀린 좌표는 없는 것보다 나쁘다
# ══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("bad", [
    [0.1, 0.2, 1.4, 0.3],          # 정규화 범위 초과 (픽셀 좌표를 넘긴 경우)
    [0.5, 0.2, 0.3, 0.4],          # x 가 뒤집힘
    [0.1, 0.2, 0.3],               # 원소 3개
    "0.1,0.2,0.3,0.4",             # 배열이 아님
])
def test_invalid_bbox_is_dropped_not_guessed(parser, bad):
    """규약을 벗어난 bbox 는 버린다 — 화면이 엉뚱한 곳을 하이라이트하면 검증이 무의미해진다."""
    p, _ = parser(vlm_payload(**{FAIL_ACTION: vlm_item(raw_value="Close", bbox=bad)}))
    rec = p.extract("x.tif", _triage(), _fields(FAIL_ACTION))[0]
    assert rec.bbox is None                                 # 값은 살리고 좌표만 버린다
    assert rec.raw_value == "Close"


def test_valid_bbox_is_kept_normalized(parser):
    """정상 bbox 는 정규화 그대로 유지된다 (계약 규약)."""
    p, _ = parser(vlm_payload(**{
        FAIL_ACTION: vlm_item(raw_value="Close", bbox=[0.12, 0.34, 0.56, 0.40]),
    }))
    rec = p.extract("x.tif", _triage(), _fields(FAIL_ACTION))[0]
    assert rec.bbox == (0.12, 0.34, 0.56, 0.40)             # 값·순서 그대로
    assert all(0.0 <= v <= 1.0 for v in rec.bbox)           # 정규화 범위


# ══════════════════════════════════════════════════════════════════
#  ⑤ 재판독 — 규정 문구와 크롭만
# ══════════════════════════════════════════════════════════════════

def test_reread_prompt_has_no_forbidden_phrase():
    """재판독 프롬프트에 '값이 틀렸다' 류 표현이 없다 (환각 유도 금지)."""
    for phrase in ("틀렸", "잘못", "다시 생각", "오류"):
        assert phrase not in REREAD_SYSTEM
    # 규정 문구가 실제로 들어 있다.
    assert "문자 그대로 무엇이 적혀 있는지" in REREAD_SYSTEM


def test_reread_without_bbox_returns_none(parser):
    """bbox 가 없으면 재판독하지 않는다.

    페이지 전체를 다시 돌리면 같은 답(무의미)이거나 다른 답을 내라는 압박(환각)이 된다.
    """
    p, client = parser(vlm_payload())
    prev = RawExtraction(field_key=FAIL_ACTION, raw_value=None, bbox=None, page=1)
    assert p.reread("x.tif", schema.get(FAIL_ACTION), prev) is None
    assert client.call_count == 0                            # 호출 자체를 하지 않는다


def test_reread_does_not_loop_internally(parser, monkeypatch, tmp_path):
    """재판독은 스스로 반복하지 않는다 (재시도 제어권은 pipeline 에 있다)."""
    p, client = parser(json.dumps({"raw_value": "Close", "raw_label": "Air Fails Valve to",
                                   "confidence": 0.8}, ensure_ascii=False))
    # 크롭 단계도 합성 이미지로 대체한다.
    png = str(tmp_path / "crop.png")
    from PIL import Image
    Image.new("L", (200, 60), color=255).save(png)
    monkeypatch.setattr(p, "_crop", lambda path, page, bbox: png)

    prev = RawExtraction(field_key=FAIL_ACTION, raw_value=None,
                         bbox=(0.1, 0.2, 0.5, 0.25), page=1)
    rec = p.reread("x.tif", schema.get(FAIL_ACTION), prev, attempt=1)
    assert rec is not None                                   # 결과가 나온다
    assert rec.raw_value == "Close"                          # 원문 그대로
    assert client.call_count == 1                            # 정확히 1회


# ══════════════════════════════════════════════════════════════════
#  ⑥ 재현성 — 캐시가 실제로 호출을 막는가
# ══════════════════════════════════════════════════════════════════

def test_cache_prevents_second_call(parser, tmp_path):
    """같은 입력을 두 번 처리하면 두 번째는 호출하지 않는다 (철학 6).

    VLM API 는 완전한 결정론이 아니므로, 캐시 없이는 재실행 결과가 흔들린다.
    """
    src = tmp_path / "doc.tif"                               # 캐시 키의 파일 해시용
    src.write_bytes(b"fixed bytes for hashing")
    cache = MemoryResponseCache()
    payload = vlm_payload(**{FAIL_ACTION: vlm_item(raw_value="Close")})
    p, client = parser(payload, cache=cache)

    first = p.extract(str(src), _triage(), _fields(FAIL_ACTION))
    second = p.extract(str(src), _triage(), _fields(FAIL_ACTION))
    assert client.call_count == 1                             # 두 번째는 캐시 적중
    assert first[0].raw_value == second[0].raw_value == "Close"   # 같은 결과


def test_no_cache_means_every_call_hits_api(parser, tmp_path):
    """캐시를 넘기지 않으면 매번 호출한다 (기본 동작이 조용히 바뀌지 않는다)."""
    src = tmp_path / "doc.tif"
    src.write_bytes(b"fixed bytes")
    p, client = parser(vlm_payload(**{FAIL_ACTION: vlm_item(raw_value="Close")}))
    p.extract(str(src), _triage(), _fields(FAIL_ACTION))
    p.extract(str(src), _triage(), _fields(FAIL_ACTION))
    assert client.call_count == 2                             # 캐시가 없으면 두 번


# ══════════════════════════════════════════════════════════════════
#  ⑦ 프롬프트가 실물 함정을 다루는가
# ══════════════════════════════════════════════════════════════════

def test_system_prompt_covers_known_traps():
    """프롬프트가 실물에서 확인된 함정을 명시한다."""
    assert "absence_reason" in SYSTEM                        # 없음 사유 3분류
    assert "row_text" in SYSTEM                              # 한 칸 여러 필드
    assert "체크박스" in SYSTEM                                # 체크박스 판독
    assert "손으로 그린" in SYSTEM                             # 손글씨 체크
    assert "만들어내지 않는다" in SYSTEM                         # 근거 없는 값 금지
