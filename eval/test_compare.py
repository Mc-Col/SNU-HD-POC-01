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

from eval.compare import (is_na, is_unreadable, looks_numeric,  # noqa: E402
                          numbers, roman_to_arabic, same, uncertain, why)


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

# ── ⑦ 깨진 단위 표기 — numeric=True 로 통째로 무시한다 ─────────────
#
#  "오타는 실제로 일부 파일은 m?h 이런식으로도 되어있어. 일단 수치에 집중하는걸로"
#  (2026-08-24). 단위 어휘에 없는 표기가 실제로 나오므로, 필드가 숫자라고
#  알려진 경우에는 글자를 어휘와 무관하게 전부 지운다.

@pytest.mark.parametrize("gold,got", [
    ("30 m3/h", "30 m?h"),              # 글자가 뭉개진 단위
    ("142.6 m3/Hr", "142.6 m?hr"),
    ("20 m3/H", "20 ㎥/h"),             # 조합 문자 (NFKC 정규화)
    ("18.6 kg/cm2G", "18.6 ㎏/㎠G"),
    ("30 m3/h", "30 m3/Hz"),            # 어휘에 없는 단위
    ("138 ℃", "138 deg"),
    ("0.1 M3/hr", "0.1"),               # 단위가 아예 없음
    ("53.8", "53.8 Cv"),
])
def test_깨진_단위도_숫자_필드면_통과한다(gold, got):
    assert same(gold, got, numeric=True), why(gold, got, numeric=True)


def test_자동_모드는_어휘에_없는_단위를_통과시키지_않는다():
    """필드 정보 없이 부를 때는 보수적으로 — 조용히 틀리는 쪽보다 낫다."""
    assert not same("30 m3/h", "30 m3/Hz")          # 자동 모드
    assert same("30 m3/h", "30 m3/Hz", numeric=True)  # 숫자 필드로 알려주면 통과


def test_숫자_필드여도_숫자가_다르면_잡는다():
    assert not same("30 m?h", "31 m?h", numeric=True)
    assert not same("95", "70.7", numeric=True)
    assert not same("0 cP", "1 cP", numeric=True)


def test_숫자_필드에_숫자가_없으면_불일치():
    """`FAIL OPEN` 을 숫자 필드로 잘못 지정해도 조용히 통과하지 않는다."""
    assert not same("FAIL OPEN", "FAIL OPEN", numeric=True)


def test_텍스트_필드로_지정하면_숫자로_뭉개지_않는다():
    assert not same("A351 CF8M", "A351 CF3M", numeric=False)
    assert not same("667-ED", "657-ED", numeric=False)
    # 단위만 다른 값을 텍스트 필드로 지정하면 불일치가 정상이다
    assert not same("30 m3/h", "30 m?h", numeric=False)


def test_재질_필드는_숫자_모드로_부르지_않는다():
    """설계 확인 — 자동 모드에서 재질이 텍스트로 판정되는가."""
    for s in ["A351 CF8M", "17-4PH SST", "316 SST + STELLITE", "WC5", "C5"]:
        assert not looks_numeric(s), f"{s} 가 숫자로 판정됨"
