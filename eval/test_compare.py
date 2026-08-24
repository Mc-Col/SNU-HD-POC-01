# -*- coding: utf-8 -*-
"""값 대조 규칙 자체 검증 — 골든셋 8건에서 실제로 관측된 표기로 만든다.

    python -m pytest eval/test_compare.py

이 파일이 지키는 것
    ① 단위 표기 변형(H·Hr·hr·m3h)을 감점하지 않는다
    ② 로마자와 아라비아 숫자를 같게 본다 (CLASS 4 == CLASS IV)
    ③ 그러면서 실제로 다른 값(FAIL OPEN vs FAIL CLOSE)은 잡는다
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from eval.compare import (is_na, is_unreadable, numbers,  # noqa: E402
                          roman_to_arabic, same, uncertain, why)


# ── ① 단위 표기 변형은 감점하지 않는다 (골든셋 실측 표기) ──────────
@pytest.mark.parametrize("gold,got", [
    ("142.6 m3/Hr", "142.6 M3/H"),      # d002/d003 vs d004 표기
    ("20 m3/H", "20 M3/hr"),            # d001 vs d007 표기
    ("30 m3h", "30 m3/h"),              # d008 — 슬래시 없는 오타
    ("18.6 kg/cm2G", "18.6 kgf/cm2G"),  # d001 vs d004 — kg vs kgf
    ("205 kg/cm2(g)", "205 kg/cm2G"),   # d008 표기
    ("138 ℃", "138 C"),
    ("1-1/2\"", "1 1/2 in"),            # d005 — 분수 표기
    ("0.933", "0.933"),
])
def test_단위_표기_변형은_감점하지_않는다(gold, got):
    assert same(gold, got), why(gold, got)


# ── ② 로마자 = 아라비아 숫자 ────────────────────────────────────
@pytest.mark.parametrize("gold,got", [
    ("CLASS 4", "CLASS IV"),            # d004/d006 vs d005/d007
    ("Class IV", "CLASS 4"),
    ("ANSI Class II", "ANSI CLASS 2"),  # d001~d003
    ("CLASS IV", "Class 4"),
])
def test_로마자와_아라비아_숫자는_같다(gold, got):
    assert same(gold, got), why(gold, got)


def test_로마자_변환():
    assert roman_to_arabic("CLASS IV") == "CLASS 4"
    assert roman_to_arabic("ANSI Class II") == "ANSI CLASS 2"
    assert roman_to_arabic("Class I") == "CLASS 1"
    # 로마자가 아닌 대문자는 건드리지 않는다
    assert "WCB" in roman_to_arabic("WCB Steel")
    assert "CF8M" in roman_to_arabic("A351 CF8M")


def test_로마자_아닌_것을_숫자로_바꾸지_않는다():
    # MIX·DIM 처럼 로마자로 읽힐 수 있는 단어를 망가뜨리면 안 된다
    for s in ["WC5", "C5", "A105", "316 SST", "17-4PH SST", "Mark One",
              "V100 SERIES", "GU-VDR", "667-ED", "DVC6200 HC"]:
        assert same(s, s), f"{s} 가 자기 자신과 다르게 판정됨"


# ── ③ 실제로 다른 값은 잡는다 ───────────────────────────────────
@pytest.mark.parametrize("gold,got", [
    ("FAIL OPEN", "FAIL CLOSE"),        # 안전 필드 — 절대 통과해선 안 된다
    ("Fail Close", "Fail Open"),
    ("667-ED", "657-ED"),               # 10FV011 의 그 함정
    ("95", "70.7"),                     # 같은 파일의 RATED CV 두 값
    ("CLASS 4", "CLASS 2"),             # 로마자 정규화가 등급을 뭉개지 않는다
    ("ANSI Class II", "Class IV"),
    ("142.6 m3/H", "142.5 m3/H"),       # 마지막 자리 차이
    ("300#", "600#"),
])
def test_다른_값은_잡는다(gold, got):
    assert not same(gold, got)
    assert why(gold, got)


# ── ④ N/A 와 판독불가 ───────────────────────────────────────────
def test_NA_는_값이_아니다():
    assert same("NA", "N/A")
    assert same("NA", "")
    assert same("NA", "없음")
    assert not same("NA", "316 SST")
    assert not same("316 SST", "NA")
    assert is_na("N/A") and is_na("-") and not is_na("0")


def test_값이_0_인_것과_없는_것은_다르다():
    """d008 의 점도 0 cP — 문서에 0 이라 적혀 있으면 0 이 정답이다."""
    assert not same("0 cP", "NA")
    assert same("0 cP", "0 cp")
    assert not same("0 cP", "1 cP")


def test_판독불가는_따로_본다():
    assert same("판독불가", "판독불가")
    assert not same("판독불가", "NA")
    assert not same("판독불가", "316 SST")
    assert is_unreadable("판독불가")


# ── ⑤ 라벨러 확신 표시 ──────────────────────────────────────────
def test_물음표는_떼고_비교하고_따로_센다():
    assert same("?316SST", "316 SST")
    assert uncertain("?316SST")
    assert not uncertain("316SST")


# ── ⑥ 텍스트 표기 변종은 여기서 풀지 않는다 ─────────────────────
def test_표기_변종은_별칭_사전의_몫이다():
    """EQ% 와 Equal Percent 는 여기서 통과시키지 않는다.

    통과시키면 `rules.yaml` 의 value_aliases 가 자라지 않는다.
    별칭을 거친 뒤 이 함수를 부르는 것이 설계다.
    """
    assert not same("EQ%", "Equal Percent")
    assert not same("Diaph.", "DIAPHRAGM")


# ── 대소문자·공백 ───────────────────────────────────────────────
def test_대소문자와_공백은_감점하지_않는다():
    assert same("metso", "METSO")
    assert same("Fail Close", "FAIL CLOSE")
    assert same("316 SST", "316SST")
    assert same("A351 CF8M", "a351  cf8m")


def test_숫자_추출():
    assert numbers("142.6 m3/Hr") == [142.6]
    assert numbers("1.16 / 2.51 / 12.2") == [1.16, 2.51, 12.2]
    assert numbers("CLASS IV") == [4.0]
    assert numbers("NA") == []
