# -*- coding: utf-8 -*-
"""허용 어휘 감사 검증. fixtures/text/kit_mini.xlsx 로만 돌린다."""
from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.parsers.text.audit_vocabulary import (allowed, audit,      # noqa: E402
                                              reaches, render, _enum)

KIT = os.path.join(ROOT, "fixtures", "text", "kit_mini.xlsx")


def test_표준값은_별칭의_to_값에서_온다():
    """허용값을 두 곳에 적으면 어긋난다 — value_aliases 한 곳에서만 온다."""
    _, flag = _enum()
    std = allowed("valve_body_rating", flag)
    assert "300#" in std
    assert "CLASS 300" not in std        # 표준형은 하나다


def test_별칭을_거쳐도_닿으면_통과다():
    _, flag = _enum()
    std = allowed("valve_body_rating", flag)
    assert reaches("valve_body_rating", "300#", std)
    assert reaches("valve_body_rating", "ANSI CLASS 300", std)   # 별칭 경유
    assert not reaches("valve_body_rating", "300 파운드", std)


def test_치수는_구두점을_살려_대조한다():
    """1/2" 와 12" 가 같은 키가 되면 반 인치가 열두 인치가 된다."""
    _, flag = _enum()
    std = allowed("valve_body_size", flag)
    assert reaches("valve_body_size", '1/2"', std)
    assert schema_norm('1/2"') != schema_norm('12"')


def schema_norm(v: str) -> str:
    from src import schema
    return schema.norm_alias(v, "valve_body_size")


def test_재질_어휘가_정답을_포괄한다():
    """flag_only 는 값을 바꾸지 않으므로 목록을 늘리는 위험이 0이다.

    빠져 있으면 맞는 값이 매번 확인필요로 떠서 검토자가 헛일을 한다.
    2026-08-26 감사에서 13칸을 채웠다 — 다시 비면 이 시험이 잡는다.
    """
    _, flag = _enum()
    for key, probes in [
        ("valve_body_material", ["Carbon Steel", "A216 WCB", "C5"]),
        ("valve_seat_material", ["SCS14A + STELL.", "410SST", "NBR"]),
        ("valve_plug_material", ["630SST", "316SS STELLITE"]),
        ("valve_cage_material", ["316SST w/ENC", "SUS 316 Cr.P1."]),
        ("valve_stem_material", ["630SST", "Std."]),
    ]:
        std = allowed(key, flag)
        missing = [p for p in probes if not reaches(key, p, std)]
        assert not missing, f"{key} 어휘 밖: {missing}"


def test_어휘_규칙이_없는_필드는_대상이_아니다():
    """감사는 enum_allowed_values 에 있는 필드만 본다 — 없는 필드까지 세면 소음이다."""
    correct, flag = _enum()
    gaps = audit(KIT)
    assert all(k in correct or k in flag for k in gaps)


def test_비어_있으면_그렇게_적는다():
    md = render({})
    assert "전부 표준값에 닿는다" in md


def test_같은_입력이면_같은_출력이다():
    assert render(audit(KIT)) == render(audit(KIT))
