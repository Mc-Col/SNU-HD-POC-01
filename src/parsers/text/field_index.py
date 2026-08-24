# -*- coding: utf-8 -*-
"""schema/fields.yaml → 라벨 텍스트 검색 인덱스.

유사표현(aliases)을 코드에 넣지 않는다. 전부 yaml 에서 읽는다.
표준명·유사표현이 늘어나면 이 파일을 고칠 필요 없이 스키마만 고치면 된다.
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


class FieldIndex:
    """정규화된 라벨 → 필드 조회."""

    def __init__(self, fields: list[dict]):
        self._by_label: dict[str, FieldHit] = {}
        self.collisions: list[tuple[str, str, str]] = []
        self.field_count = len(fields)

        for f in fields:                                   # yaml 순서 = 우선순위
            self._put(normalize_label(f["name"]), f, CONF_NAME, "name")
        for f in fields:                                   # 표준명이 유사표현보다 우선
            for a in f.get("aliases") or []:
                self._put(normalize_label(a), f, CONF_ALIAS, "alias")

    def _put(self, label: str, f: dict, conf: float, how: str) -> None:
        if not label:
            return
        prev = self._by_label.get(label)
        if prev is not None:
            if prev.key != f["key"]:
                self.collisions.append((label, prev.key, f["key"]))
            return                                          # 먼저 등록된 쪽이 이긴다
        self._by_label[label] = FieldHit(f["key"], f["name"], conf, how)

    @classmethod
    def load(cls, path: str = SCHEMA_PATH) -> "FieldIndex":
        with open(path, encoding="utf-8") as fp:
            doc = yaml.safe_load(fp)
        return cls(doc["fields"])

    def lookup(self, label: object) -> FieldHit | None:
        return self._by_label.get(normalize_label(label))

    def __len__(self) -> int:
        return len(self._by_label)
