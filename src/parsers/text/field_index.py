# -*- coding: utf-8 -*-
"""③-a TEXT PARSER — schema/fields.yaml → 라벨 텍스트 검색 인덱스.

유사표현(aliases)을 코드에 넣지 않는다. 전부 yaml 에서 읽는다.
표준명·유사표현이 늘어나면 이 파일을 고칠 필요 없이 스키마만 고치면 된다.

■ 한 이름이 여러 필드에 걸릴 수 있다 (2026-08-25, 구역 인식)
────────────────────────────────────────────────────────────────────
데이터시트는 부품별로 묶여 있어서 같은 항목명이 되풀이된다.

    "Maker"  → 밸브 제조사일 수도, 포지셔너 제조사일 수도 있다
    "Model"  → 밸브 본체 모델일 수도, 포지셔너 모델일 수도 있다

예전에는 이런 표기를 사전에서 아예 뺐다. 넣으면 한 필드가 이기고 나머지가
굶기 때문이다. 그 대가로 MVP 필드 `MANUFACTURER` 는 유사표현이 0개였다.

이제는 **여러 필드에 등록해 두고 구역으로 고른다**. 문서가 이 항목을 어느
부품 묶음에 넣었는지는 `sections.py` 가 알려준다.

    lookup("Maker")                          → None   (애매하면 만들지 않는다)
    lookup("Maker",     allowed=본체 필드들)  → manufacturer
    lookup("Maker",     allowed=포지셔너들)   → positioner_manufacturer
    lookup("Model No.", allowed=액추에이터들) → None   ← 오답이 여기서 죽는다

마지막 줄이 중요하다. 후보가 하나뿐이어도 그 구역에서 나올 수 없는 필드면
버린다. 액추에이터 묶음의 `Model No.`(880)를 밸브 모델로 집던 오답이 이것이다.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
SCHEMA_PATH = os.path.join(ROOT, "schema", "fields.yaml")

# 표준명 일치 / 유사표현 일치 — 고정값이다 (같은 입력 → 같은 출력)
CONF_NAME = 1.0
CONF_ALIAS = 0.95

_NON_ALNUM = re.compile(r"[^0-9A-Z]+")

# 라벨 뒤에 붙는 기호 주석 — `Inlet Pressure (P1)` · `Dynamic Viscosity (Mu)`.
# 항목명이 아니라 계산식에서 쓰는 기호다 (실물 22PCV013 에서 8칸이 이것 때문에
# 미추출이었다). 앞뒤 어디에 붙든 떼고 한 번 더 찾아본다.
_PAREN = re.compile(r"\([^()]*\)")


def normalize_label(text: object) -> str:
    """라벨 표기 흔들림을 흡수한다.

    "Req'd Flow Coeff., Cv" → "REQDFLOWCOEFFCV"
    "Model No."             → "MODELNO"
    공백·마침표·괄호·대소문자 차이는 같은 라벨로 본다.
    """
    if text is None:
        return ""
    return _NON_ALNUM.sub("", str(text).upper())


@dataclass(frozen=True)
class FieldHit:
    key: str
    name: str
    confidence: float
    matched_on: str          # "name" | "alias"
    section: str | None = None
    # 구역 한정 표기. 구역 접두어를 뗀 표기는 그 구역 안에서만 쓴다 —
    # `MATERIAL Body/Bonnet` 에서 뗀 `Body/Bonnet` 은 재질 묶음에서만 유효하다.
    # 아무 데서나 쓰면 `BODY Size` 에서 뗀 `Size` 가 액추에이터 크기를 집는다.


class FieldIndex:
    """정규화된 라벨 → 필드 후보들."""

    def __init__(self, fields: list[dict], section_names: dict[str, str] | None = None):
        # 한 라벨에 후보가 여러 개일 수 있다. 등록 순서가 곧 우선순위다
        # (표준명이 먼저, 그다음 yaml 에 적힌 순서의 유사표현).
        self._by_label: dict[str, list[FieldHit]] = {}
        self.collisions: list[tuple[str, str, str]] = []
        self.field_count = len(fields)

        for f in fields:                                   # yaml 순서 = 우선순위
            self._put(normalize_label(f["name"]), f, CONF_NAME, "name")
        for f in fields:                                   # 표준명이 유사표현보다 우선
            for a in f.get("aliases") or []:
                self._put(normalize_label(a), f, CONF_ALIAS, "alias")
                self._put_stripped(a, f, section_names or {})

    def _put_stripped(self, alias: str, f: dict, section_names: dict[str, str]) -> None:
        """구역 접두어를 뗀 표기도 등록한다 (그 구역 안에서만 쓰도록).

        킷의 원문라벨은 라벨러가 구역명을 앞에 붙여 적은 것이 많다.

            사전            "MATERIAL Body/Bonnet"
            문서의 실제 라벨  "Body /Bonnet"      ← 구역명은 여백에 세로로 따로 선다

        그대로는 영영 만나지 못한다. 실물 9건 중 15칸이 이 이유로 미추출이었다.
        구역 인식이 생겼으니 접두어를 떼고 **그 구역 한정**으로 등록할 수 있다.
        """
        n = normalize_label(alias)
        for sname, skey in section_names.items():
            if not sname or not n.startswith(sname):
                continue
            rest = n[len(sname):]
            if len(rest) < 3:            # 접두어를 떼고 남은 게 없으면 표기가 아니다
                continue
            self._put(rest, f, CONF_ALIAS, "alias", section=skey)

    def _put(self, label: str, f: dict, conf: float, how: str,
             section: str | None = None) -> None:
        if not label:
            return
        hits = self._by_label.setdefault(label, [])
        if any(h.key == f["key"] and h.section == section for h in hits):
            return                                          # 같은 필드 중복 등록
        if hits:
            # 여러 필드에 걸린 표기. 구역 없이는 여전히 매핑하지 않으므로
            # 오답이 되지는 않지만, 사전을 검토할 때 보이도록 남긴다.
            self.collisions.append((label, hits[0].key, f["key"]))
        hits.append(FieldHit(f["key"], f["name"], conf, how, section))

    @classmethod
    def load(cls, path: str = SCHEMA_PATH,
             section_names: dict[str, str] | None = None) -> "FieldIndex":
        """section_names 를 주면 구역 접두어를 뗀 표기까지 등록한다.

        `SectionIndex.name_map()` 을 그대로 넘기면 된다. 넘기지 않으면 예전과
        똑같이 동작한다 (구역을 모르는 호출부·도구가 그대로 돌아간다).
        """
        with open(path, encoding="utf-8") as fp:
            doc = yaml.safe_load(fp)
        return cls(doc["fields"], section_names)

    def lookup(self, label: object,
               allowed: set[str] | None = None,
               section: str | None = None) -> FieldHit | None:
        """라벨 → 필드. 애매하면 None (틀린 값을 만들지 않는다).

        section
            이 위치의 표준 구역 (`body` · `trim` …). 구역 한정 표기를 쓸지
            정하는 데 쓴다. 모르면 None.
        allowed
            이 위치(구역)에서 나올 수 있는 field key 집합.
            None  구역을 모른다 → 이름만으로 판단한다 (구역 구조가 없는 문서)
            집합  구역을 안다   → 그 집합 밖의 필드는 후보에서 버린다.
                  빈 집합이면 이 구역에서는 아무 필드도 나오지 않는다는 뜻이다
                  (LIMIT SW · ACCESSORIES 같은 우리 스키마 밖 묶음)
        """
        hits = self._by_label.get(normalize_label(label))
        if not hits:
            # 괄호 주석을 떼고 다시 본다. 예외 경로이므로 정확히 일치하는
            # 표기가 있으면 그쪽이 언제나 이긴다.
            bare = _PAREN.sub(" ", str(label or ""))
            if normalize_label(bare) != normalize_label(label):
                hits = self._by_label.get(normalize_label(bare))
        if not hits:
            return None

        if section is None:
            # 구역을 모르면 구역 한정 표기는 쓰지 않는다.
            hits = [h for h in hits if h.section is None]
        else:
            hits = [h for h in hits if h.section in (None, section)]
            # 이 구역 전용 표기가 있으면 그쪽이 먼저다
            hits.sort(key=lambda h: h.section != section)
        if not hits:
            return None

        if allowed is not None:
            hits = [h for h in hits if h.key in allowed]
            if not hits:
                return None                 # 이 구역에서 나올 수 없는 항목이다
            return hits[0]                  # 구역이 갈라줬다 — 우선순위대로

        if len(hits) == 1:
            return hits[0]

        # 후보가 여럿이어도 **표준명 소유 필드가 있으면 그쪽**이다.
        # `Model No.` 는 MODEL NO. 의 이름이고, POSITIONER MODEL NO. 는 그 이름을
        # 빌려 쓸 뿐이다. 이 규칙이 없으면 킷 열 이름("MODEL NO.")조차 애매해져
        # 채점에서 통째로 빠진다 (실제로 채점 칸이 205 → 196 으로 줄었다).
        if hits[0].matched_on == "name":
            return hits[0]

        # 유사표현끼리 걸린 경우는 고르지 않는다. 반쯤 맞는 값보다 미추출이
        # 낫다 — 미추출은 사람이 채우고, 오답은 그대로 흘러간다.
        return None

    def candidates(self, label: object) -> list[FieldHit]:
        """구역과 무관한 후보 전부. 리포트·진단용."""
        return list(self._by_label.get(normalize_label(label)) or [])

    def keys(self) -> set[str]:
        """스키마에 존재하는 field key 집합."""
        return {h.key for hits in self._by_label.values() for h in hits}

    def __len__(self) -> int:
        return len(self._by_label)
