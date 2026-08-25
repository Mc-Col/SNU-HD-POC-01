# -*- coding: utf-8 -*-
"""단위 토큰 판별 — 목록은 코드가 아니라 schema/rules.yaml 에 있다.

라벨과 값 사이에 단위 칸이 끼는 양식이 있다.
  "Flow Rate | m3/h | … | 20"   ← m3/h 를 값으로 집으면 안 된다
"""
from __future__ import annotations

import os

import yaml

from .field_index import normalize_label

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
RULES_PATH = os.path.join(ROOT, "schema", "rules.yaml")


class UnitIndex:
    def __init__(self, tokens: list[str]):
        self.raw = list(tokens)
        self._norm = {normalize_label(t) for t in tokens if normalize_label(t)}
        self._exact = {str(t).strip().upper() for t in tokens}

    @classmethod
    def load(cls, path: str = RULES_PATH) -> "UnitIndex":
        if not os.path.exists(path):
            return cls([])
        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
        return cls(doc.get("unit_tokens") or [])

    def is_annotation(self, text: object) -> bool:
        """괄호로 감싸이고 숫자가 없으면 단위 주석이다.

        깨진 단위 표기는 어휘로 잡을 수 없다 — 실물 19FV077 의
        `Inlet Press | (kg/cm?(g)) | 205.000` 처럼 글자가 깨져 들어온다.
        어휘 대신 모양으로 거른다.
        """
        t = str(text or "").strip()
        return (len(t) > 2 and t.startswith("(") and t.endswith(")")
                and not any(ch.isdigit() for ch in t))

    def is_unit(self, text: object) -> bool:
        t = str(text or "").strip()
        if not t:
            return False
        if t.upper() in self._exact:
            return True
        n = normalize_label(t)
        return bool(n) and n in self._norm or self.is_annotation(t)

    def __len__(self) -> int:
        return len(self._norm)
