# -*- coding: utf-8 -*-
"""라벨 하나가 표준 필드 여러 개를 덮는 칸을 쪼갠다.

규칙은 코드가 아니라 schema/rules.yaml 의 composite_labels 절에 있다.
스키마에 아직 없는 field key 를 가리키는 조각은 버리지 않고 미매핑으로 돌려준다.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

import yaml

from .field_index import CONF_ALIAS, FieldIndex, normalize_label

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
RULES_PATH = os.path.join(ROOT, "schema", "rules.yaml")


@dataclass(frozen=True)
class Piece:
    """쪼갠 조각 하나."""
    field_key: str | None        # 스키마에 있는 키. 없으면 None
    label: str                   # 이 조각의 원문 라벨
    value: str
    confidence: float = CONF_ALIAS


@dataclass(frozen=True)
class CompositeRule:
    label: str
    separator: str | None
    fields: tuple[str | None, ...]
    pattern: re.Pattern | None
    note: str

    def split(self, label_text: str, value_text: str) -> list[Piece] | None:
        """쪼갠 조각들. 규칙에 맞지 않으면 None (미매핑으로 남긴다)."""
        if self.pattern is not None:
            m = self.pattern.fullmatch(value_text.strip())
            if m is None:
                return None
            return [
                Piece(key, f"{label_text} → {key}", v.strip())
                for key, v in m.groupdict().items() if v and v.strip()
            ]

        sep = self.separator or "/"
        vals = [v.strip() for v in value_text.split(sep)]
        if len(vals) != len(self.fields):
            return None                       # 조각 수가 안 맞으면 손대지 않는다
        labs = [x.strip() for x in self.label.split(sep)]
        if len(labs) != len(self.fields):
            labs = [label_text] * len(self.fields)

        out: list[Piece] = []
        for key, lab, val in zip(self.fields, labs, vals):
            if key is None or not val:
                continue                      # 대응 필드 없음 → 버린다 (규칙에 명시됨)
            out.append(Piece(key, lab, val))
        return out                            # 빈 리스트 = 규칙대로 전부 버림


class CompositeIndex:
    """정규화된 라벨 → 분해 규칙."""

    def __init__(self, rules: list[CompositeRule]):
        self._by_label = {normalize_label(r.label): r for r in rules}
        self.rules = rules

    @classmethod
    def load(cls, path: str = RULES_PATH) -> "CompositeIndex":
        if not os.path.exists(path):
            return cls([])
        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
        out: list[CompositeRule] = []
        for raw in doc.get("composite_labels") or []:
            pat = raw.get("pattern")
            out.append(CompositeRule(
                label=str(raw["label"]),
                separator=raw.get("separator"),
                fields=tuple(raw.get("fields") or ()),
                pattern=re.compile(pat) if pat else None,
                note=str(raw.get("note") or "").strip(),
            ))
        return cls(out)

    def lookup(self, label: object) -> CompositeRule | None:
        return self._by_label.get(normalize_label(label))

    def __len__(self) -> int:
        return len(self._by_label)


def resolve(pieces: list[Piece], index: FieldIndex) -> tuple[list[Piece], list[Piece]]:
    """스키마에 있는 조각 / 아직 없는 조각으로 나눈다."""
    known = {f for f in index.keys()}
    ok = [p for p in pieces if p.field_key in known]
    pending = [p for p in pieces if p.field_key not in known]
    return ok, pending


def try_split(
    composite: "CompositeIndex",
    fields: FieldIndex,
    label: str,
    value: str,
) -> tuple[list[Piece], list[Piece]] | None:
    """복합 라벨이면 (스키마에 있는 조각, 아직 없는 조각) 을 돌려준다.

    복합 라벨이 아니거나 규칙에 맞지 않으면 None — 호출자가 평소대로 처리하면 된다.
    """
    if not value:
        return None
    rule = composite.lookup(label)
    if rule is None:
        return None
    pieces = rule.split(label, value)
    if pieces is None:
        return None
    return resolve(pieces, fields)
