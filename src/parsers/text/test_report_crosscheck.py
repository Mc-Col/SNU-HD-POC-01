# -*- coding: utf-8 -*-
"""대조 집계 검증 — API 호출 없이 순수 계산만 본다.

파이프라인을 돌리는 부분(`run()`)은 VLM 비용이 들어 테스트에서 부르지 않는다.
집계·판정·표 만들기는 순수 함수라 여기서 못으로 박을 수 있다.
"""
import os
import sys
from collections import Counter

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.contracts import FieldRecord                                  # noqa: E402
from src.parsers.text.crosscheck import (AGREE, CONFLICT, NOTATION,    # noqa: E402
                                         TEXT_ONLY, VLM_ONLY, Agreement)
from src.parsers.text.report_crosscheck import (_truth, agreement,     # noqa: E402
                                                compare, render)


def _rec(key, value):
    return FieldRecord(doc_id="d", field_key=key, field_name=key, value=value)


def test_단위_표기_차이는_맞은_것으로_센다():
    """비교를 새로 만들지 않고 eval/compare 를 쓰는지 확인한다."""
    hit, miss, bad = compare({"normal_flow_rate": "142.6 m3/Hr"},
                             [_rec("normal_flow_rate", "142.6")])
    assert (hit, miss, bad) == (1, 0, [])


def test_값이_없는_칸은_세지_않는다():
    """미추출을 '틀림' 으로 세면 정확도와 추출률이 뒤섞인다."""
    hit, miss, _ = compare({"rated_cv": "110"}, [_rec("rated_cv", None)])
    assert (hit, miss) == (0, 0)


def test_틀린_칸은_정답과_함께_남긴다():
    hit, miss, bad = compare({"model_no": "2121"}, [_rec("model_no", "880")])
    assert (hit, miss) == (0, 1)
    assert bad == [("model_no", "2121", "880")]


def test_합의율_분모에서_한쪽만_읽은_칸을_뺀다():
    """텍스트가 약한 문서에서 비율이 임의로 오르내리지 않게 한다."""
    ok, no = agreement([Agreement("a", AGREE), Agreement("b", NOTATION),
                        Agreement("c", CONFLICT), Agreement("d", VLM_ONLY),
                        Agreement("e", TEXT_ONLY)])
    assert (ok, no) == (2, 1)          # 표기차이는 같은 값으로 센다


def test_엑셀이_숫자로_저장한_정답을_되돌린다():
    """사람이 `300` 이라 적었는데 엑셀이 숫자로 바꿔 `300.0` 으로 읽히는 문제."""
    row = {"truth": {"valve_body_rating": (300.0, "VALVE BODY RATING"),
                     "rated_cv": (53.8, "RATED CV"),
                     "model_no": ("?2121", "MODEL NO."),
                     "manufacturer": ("N/A", "MANUFACTURER"),
                     "fluid_name": (None, "FLUID NAME")}}
    got = _truth(row)
    assert got["valve_body_rating"] == "300"        # 소수점 없이
    assert got["rated_cv"] == "53.8"               # 정수가 아니면 그대로
    assert got["model_no"] == "2121"               # 확신 없음 표시(?)는 뗀다
    assert "manufacturer" not in got               # N/A 는 채점 대상 아님
    assert "fluid_name" not in got                 # 빈 칸도 아님


def test_처리_실패를_표에_드러낸다():
    """Triage 가 범위 밖으로 판정한 문서가 조용히 빠지면 정확도가 부풀려진다."""
    md = render([{"doc": "d013", "file": "44LV001.xlsx", "state": "처리 실패",
                  "why": "out_of_scope: 사양표 페이지 없음"}],
                Counter({"실패": 1}), use_vlm=True)
    assert "처리 실패" in md and "d013" in md and "사양표 페이지 없음" in md


def test_대조가_없으면_없다고_적는다():
    md = render([], Counter(), use_vlm=False)
    assert "두 경로 대조 없음" in md
