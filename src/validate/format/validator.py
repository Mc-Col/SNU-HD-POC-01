# -*- coding: utf-8 -*-
"""⑤-a 형식·허용값 검증 — 판정과 사유만 낸다.

형식 위반에는 재시도를 걸지 않는다. 재시도는 추출 실패(못 읽음)에만 해당한다.
값이 틀렸다고 모델에게 알리면 환각을 유도한다.

검사 규칙은 코드가 아니라 schema/*.yaml 에 둔다.
  - required        : schema/fields.yaml 의 required
  - numeric/pattern : schema/rules.yaml 의 format_rules (없으면 검사하지 않는다)
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

import yaml

from src.contracts import FailureKind

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
FIELDS_PATH = os.path.join(ROOT, "schema", "fields.yaml")
RULES_PATH = os.path.join(ROOT, "schema", "rules.yaml")

# 값 없음으로 취급하는 표기
EMPTY_TOKENS = {"", "N/A", "NA", "-", "없음", "판독불가"}

_NUMERIC = re.compile(r"[-+]?\d+(?:[.,]\d+)?")


@dataclass(frozen=True)
class Violation:
    field_key: str
    field_name: str
    rule: str                       # required / numeric / pattern / max_length / unknown_field
    reason: str
    value: str | None = None
    kind: FailureKind = FailureKind.FORMAT

    def as_note(self) -> str:
        return f"[{self.rule}] {self.reason}"


def load_fields(path: str = FIELDS_PATH) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)["fields"]


def load_format_rules(path: str = RULES_PATH) -> dict[str, dict]:
    """schema/rules.yaml 의 format_rules. 아직 없으면 빈 표를 돌려준다."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    return doc.get("format_rules") or {}


def _is_empty(v: object) -> bool:
    return v is None or str(v).strip().upper() in {t.upper() for t in EMPTY_TOKENS}


def validate_format(
    values: dict[str, object],
    fields: list[dict] | None = None,
    rules: dict[str, dict] | None = None,
) -> list[Violation]:
    """정규화된 값 묶음 → 위반 목록. 위반이 없으면 빈 리스트."""
    fields = fields if fields is not None else load_fields()
    rules = rules if rules is not None else load_format_rules()
    by_key = {f["key"]: f for f in fields}

    out: list[Violation] = []

    # 계약에 없는 키 — 조용히 넘기지 않는다
    for k in sorted(set(values) - set(by_key)):
        out.append(Violation(k, "?", "unknown_field",
                             "schema/fields.yaml 에 없는 field_key", _s(values[k])))

    for f in fields:                                    # yaml 순서 = 출력 순서
        key = f["key"]
        if key not in values:
            if f["required"]:
                out.append(Violation(key, f["name"], "required",
                                     "필수 필드가 아예 오지 않음"))
            continue

        raw = values[key]
        if _is_empty(raw):
            if f["required"]:
                out.append(Violation(key, f["name"], "required",
                                     "필수 필드에 값이 없음", _s(raw)))
            continue

        rule = rules.get(key) or {}
        text = str(raw).strip()

        if rule.get("numeric") and not _NUMERIC.fullmatch(text.replace(" ", "")):
            out.append(Violation(key, f["name"], "numeric",
                                 "숫자여야 하는 필드에 숫자가 아닌 값", text))

        pat = rule.get("pattern")
        if pat and not re.fullmatch(pat, text):
            out.append(Violation(key, f["name"], "pattern",
                                 f"형식 불일치 (기대: {pat})", text))

        cap = rule.get("max_length")
        if cap and len(text) > int(cap):
            out.append(Violation(key, f["name"], "max_length",
                                 f"{cap}자를 넘음 ({len(text)}자)", text))

    return out


def _s(v: object) -> str | None:
    return None if v is None else str(v)
