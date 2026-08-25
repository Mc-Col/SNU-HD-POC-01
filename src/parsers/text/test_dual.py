# -*- coding: utf-8 -*-
"""③ 두 경로 파서 검증.

파이프라인이 요구하는 계약(`ParserModule`)을 지키는지, 그리고 대조 결과를
**어떻게 계약 ②(RawExtraction) 하나로 접는지**를 본다.

핵심 불변식 셋:
  · 불일치는 confidence 0 으로 내려 사람에게 보낸다 (새 상태를 만들지 않는다)
  · 일치했다고 confidence 를 올리지 않는다 (자동확정 문턱을 낮추면 안 된다)
  · 텍스트 파서가 넘어져도 VLM 경로는 계속된다
"""
import json
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import schema                                        # noqa: E402
from src.contracts import RawExtraction                       # noqa: E402
from src.parsers.text import crosscheck as cc                 # noqa: E402
from src.parsers.text.dual import DualParser                  # noqa: E402

MOCK = os.path.join(ROOT, "fixtures", "vlm", "mock_responses", "clean_extraction.json")
FIELDS = schema.mvp_fields()


class _Stub:
    """계약만 지키는 가짜 파서. {field_key: 값} 을 주면 그대로 낸다."""

    def __init__(self, values: dict, confidence: float = 0.95, boom: bool = False):
        self.values, self.confidence, self.boom = values, confidence, boom
        self.reread_calls = 0

    def extract(self, path, triage, fields):
        if self.boom:
            raise RuntimeError("판독 실패")
        return [RawExtraction(field_key=f.key, raw_value=self.values.get(f.key),
                              raw_label="라벨" if f.key in self.values else None,
                              source_locator=f"stub!{f.key}",
                              confidence=self.confidence if f.key in self.values else 0.0)
                for f in fields]

    def reread(self, path, f, prev, attempt=1):
        self.reread_calls += 1
        return RawExtraction(field_key=f.key, raw_value="재판독", confidence=0.9)


def _by_key(recs):
    return {r.field_key: r for r in recs}


def test_요청한_필드를_빠짐없이_돌려준다():
    dual = DualParser(vlm=_Stub({"model_no": "667-ED"}), text=_Stub({"model_no": "667-ED"}))
    got = dual.extract("x.pdf", None, FIELDS)
    assert [r.field_key for r in got] == [f.key for f in FIELDS]
    assert all(isinstance(r, RawExtraction) for r in got)


def test_불일치는_확신도를_내려_사람에게_보낸다():
    """새 상태를 만들지 않는다 — 파이프라인 `_decide()` 의 임계 미달 경로를 쓴다."""
    dual = DualParser(vlm=_Stub({"model_no": "667-ED"}, confidence=0.97),
                      text=_Stub({"model_no": "880"}))
    r = _by_key(dual.extract("x.pdf", None, FIELDS))["model_no"]
    assert r.raw_value == "667-ED"            # 값은 VLM 것을 남긴다 (필드 배정은 VLM 몫)
    assert r.confidence == 0.0                # → REVIEW
    assert "880" in r.note and "사람 확인 필요" in r.note


def test_일치했다고_확신도를_올리지_않는다():
    dual = DualParser(vlm=_Stub({"rated_cv": "236"}, confidence=0.60),
                      text=_Stub({"rated_cv": "236.0"}))
    r = _by_key(dual.extract("x.pdf", None, FIELDS))["rated_cv"]
    assert r.confidence == 0.60                       # 그대로
    assert "같은 값" in r.note


def test_표기차이는_사람을_부르지_않는다():
    """`300#` 과 `ANSI CLASS 300` 은 같은 등급이다."""
    dual = DualParser(vlm=_Stub({"valve_body_rating": "300#"}, confidence=0.9),
                      text=_Stub({"valve_body_rating": "ANSI CLASS 300"}))
    r = _by_key(dual.extract("x.pdf", None, FIELDS))["valve_body_rating"]
    assert r.confidence == 0.9
    assert "표준형 미정" in r.note


def test_VLM_이_놓친_필드는_텍스트_후보로_채운다():
    """빈칸보다 후보가 낫다. 단 confidence 0 이라 자동확정되지 않는다."""
    dual = DualParser(vlm=_Stub({}), text=_Stub({"model_no": "2121"}))
    r = _by_key(dual.extract("x.pdf", None, FIELDS))["model_no"]
    assert r.raw_value == "2121"
    assert r.confidence == 0.0
    assert r.source_locator == "stub!model_no"        # 근거는 텍스트 쪽 좌표
    assert "VLM 미추출" in r.note


def test_후보_채우기를_끌_수_있다():
    dual = DualParser(vlm=_Stub({}), text=_Stub({"model_no": "2121"}),
                      fill_from_text=False)
    r = _by_key(dual.extract("x.pdf", None, FIELDS))["model_no"]
    assert r.raw_value is None
    assert "텍스트에서만 읽힘" in r.note               # 사실은 남는다


def test_VLM_값을_텍스트로_덮지_않는다():
    """필드 배정은 VLM 이 정한다 — 텍스트가 다른 값을 내도 값은 바뀌지 않는다."""
    dual = DualParser(vlm=_Stub({"manufacturer": "FISHER"}),
                      text=_Stub({"manufacturer": "NIIGATA MASONEILAN"}))
    r = _by_key(dual.extract("x.pdf", None, FIELDS))["manufacturer"]
    assert r.raw_value == "FISHER"


def test_텍스트_파서가_넘어져도_VLM_경로는_계속된다():
    dual = DualParser(vlm=_Stub({"model_no": "667-ED"}), text=_Stub({}, boom=True))
    r = _by_key(dual.extract("x.pdf", None, FIELDS))["model_no"]
    assert r.raw_value == "667-ED"
    assert "텍스트 파서 실패" in r.note                # 조용히 넘기지 않는다


def test_담당이_아닌_확장자면_텍스트_파서를_부르지_않는다():
    """스캔 tif 는 텍스트 층이 없다. 부르면 실패 로그만 쌓인다."""
    text = _Stub({"model_no": "부르면 안 됨"}, boom=True)
    dual = DualParser(vlm=_Stub({"model_no": "667-ED"}), text=text)
    r = _by_key(dual.extract("scan.tif", None, FIELDS))["model_no"]
    assert r.raw_value == "667-ED"
    assert "텍스트 파서 실패" not in r.note


def test_재판독은_VLM_에_맡긴다():
    vlm = _Stub({"model_no": "667-ED"})
    dual = DualParser(vlm=vlm, text=_Stub({}))
    got = dual.reread("x.pdf", FIELDS[0], RawExtraction(field_key="x", raw_value=None))
    assert got is not None and vlm.reread_calls == 1


def test_대조_집계를_남긴다():
    """로그·발표 숫자로 쓴다."""
    dual = DualParser(vlm=_Stub({"model_no": "667-ED", "rated_cv": "236"}),
                      text=_Stub({"model_no": "880", "required_cv": "126"}))
    dual.extract("x.pdf", None, FIELDS)
    s = dual.last_summary
    assert s[cc.CONFLICT] == 1 and s[cc.VLM_ONLY] == 1 and s[cc.TEXT_ONLY] == 1
    assert len(dual.last_agreements) == len(FIELDS)


def test_실제_mock_응답으로도_돈다():
    with open(MOCK, encoding="utf-8") as f:
        vals = {e["field_key"]: e["raw_value"] for e in json.load(f)["extractions"]
                if e.get("raw_value")}
    dual = DualParser(vlm=_Stub(vals), text=_Stub(dict(vals)))
    got = dual.extract("x.pdf", None, FIELDS)
    agreed = [r for r in got if "같은 값" in r.note]
    assert agreed, "같은 값을 넣었으면 일치가 나와야 한다"


def test_같은_입력이면_같은_출력이다():
    def run():
        dual = DualParser(vlm=_Stub({"model_no": "667-ED"}), text=_Stub({"model_no": "880"}))
        return [(r.field_key, r.raw_value, r.confidence, r.note)
                for r in dual.extract("x.pdf", None, FIELDS)]
    assert run() == run()
