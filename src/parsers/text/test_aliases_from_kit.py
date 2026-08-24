# -*- coding: utf-8 -*-
"""킷 원문라벨 → 유사표현 후보 추출 검증."""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.parsers.text.aliases_from_kit import clean_label, collect, render  # noqa: E402

KIT = os.path.join(ROOT, "fixtures", "text", "kit_mini.xlsx")


@pytest.fixture(scope="module")
def res():
    return collect(KIT)


# ── 라벨 정제 ──────────────────────────────────────────────────
@pytest.mark.parametrize("raw,clean", [
    ("Model No.", "Model No."),
    ("Body Model(Type)", "Body Model(Type)"),          # 붙여 쓴 괄호는 라벨의 일부
    ("End Connection (Casting Rating이 공란일 경우 동일)", "End Connection"),
    ("(하단 꼬리말)", ""),                              # 위치 설명
    ("NA (문서 좌측 상단)", ""),                         # 항목 없음 표기
    ("N/A", ""),
    ("", ""),
])
def test_원문라벨_정제(raw, clean):
    assert clean_label(raw) == clean


# ── 수집 ───────────────────────────────────────────────────────
def test_새_표기만_모은다(res):
    assert {k: [c.text for c in v] for k, v in res.by_field.items()} == {
        "ENGINEERING TAG NO.": ["Tag No."],
        "ACTUATOR FAIL ACTION": ["Fail/Air-To"],
    }


def test_이미_등록된_표기는_후보에서_뺀다(res):
    already = {t for _, t in res.already}
    assert {"Tag", "Manufacturer", "Model No.", "Fail Position"} <= already


def test_위치_설명은_사전에_넣지_않는다(res):
    """킷 규칙 — 괄호로 시작하면 표기 변종이 아니라 위치 설명이다."""
    assert res.positional == [("MANUFACTURER", "(하단 꼬리말)")]


def test_대응_필드가_없는_킷_컬럼을_드러낸다(res):
    """2026-08-24 — RATED CV 가 스키마에 생겨 대응되지 않는 이름이 없다.
    비어 있음을 단정해 스키마 변경으로 대응이 깨지면 먼저 알려주게 한다."""
    assert res.unresolved_fields == []


def test_스키마를_고치지_않는다(res):
    """이 도구는 후보만 낸다. 반영은 output_sample.xlsx → gen_schema.py."""
    md, _ = render(res)
    assert "output_sample.xlsx" in md and "gen_schema.py" in md


def test_붙여넣기용_TSV_를_낸다(res):
    _, tsv = render(res)
    head, *rows = tsv.strip().splitlines()
    assert head.split("\t") == ["표준필드", "유사표현", "건수", "채택(O/X)"]
    assert len(rows) == res.total


def test_같은_입력이면_같은_출력이다():
    a, b = collect(KIT), collect(KIT)
    assert [(k, [c.text for c in v]) for k, v in a.by_field.items()] == \
           [(k, [c.text for c in v]) for k, v in b.by_field.items()]
