# -*- coding: utf-8 -*-
"""⑤-a 형식·허용값 검증 — 판정과 사유만 낸다.

■ 무슨 작업인가
────────────────────────────────────────────────────────────────────
④ Normalize 를 지난 값이 **형식으로 말이 되는가**만 본다. 값이 옳은지는
⑤-b 도메인 검증과 사람의 몫이다. 형식 위반에는 재시도를 걸지 않는다 —
재시도는 "못 읽음" 에만 해당하고, 값이 틀렸다고 모델에게 알리면 환각을 유도한다.

검사 규칙은 코드가 아니라 schema/*.yaml 에 둔다 (개발 철학 2).
  - required          : `schema/fields.yaml` 의 required
  - numeric/max_length: `schema/rules.yaml` 의 format_rules
  - tag               : 태그 명명 규칙은 `preprocess.parse_tag()` 가 이미 안다

■ numeric 이 단위를 위반으로 보지 않는 이유 (2026-08-25)
────────────────────────────────────────────────────────────────────
옛 데이터시트는 단위를 사람이 손으로 썼다. `142.6 m3/Hr` · `205 kg/cm2(g)` ·
`20 m3/H` 가 전부 정상이고, 글자가 깨진 `(m?h)` 도 실물에 있다. 단위 표기를
위반으로 잡으면 측정하려는 것(값을 제대로 읽었나)이 아니라 필사 습관을 재게 된다.
**숫자가 하나라도 있으면 통과, 하나도 없으면 위반**으로 본다.

이 판정선은 골든셋 20건 실측에서 나왔다 — 아래 9개 필드는 정답이 **전부**
숫자를 포함했다. 반대로 `RF FLANGED`(연결 형식)가 밸브 등급 칸에 들어오는
실제 오답이 있었고, 그런 것이 여기서 잡힌다.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

import yaml

from src.contracts import FailureKind
from src.preprocess import parse_tag        # 태그 규칙을 다시 만들지 않는다

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
FIELDS_PATH = os.path.join(ROOT, "schema", "fields.yaml")
RULES_PATH = os.path.join(ROOT, "schema", "rules.yaml")

# 값 없음으로 취급하는 표기
EMPTY_TOKENS = {"", "N/A", "NA", "-", "없음", "판독불가"}

_HAS_DIGIT = re.compile(r"\d")
# 숫자 필드에 한글이 섞이면 설명이 딸려 들어온 것이다 (`약 53.8`).
# 단위는 전부 ASCII·기호(`m3/Hr` · `℃` · `㎏/㎠`)이므로 한글은 단위일 수 없다.
# 골든셋 숫자 필드 정답 143칸에 한글은 0건이었다 (2026-08-25 실측).
_HANGUL = re.compile(r"[가-힣]")


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

        if rule.get("numeric"):
            # 단위 표기는 위반이 아니다 — 숫자가 아예 없을 때, 또는 단위일 수
            # 없는 한글이 섞였을 때만 잡는다
            if not _HAS_DIGIT.search(text):
                out.append(Violation(key, f["name"], "numeric",
                                     "숫자가 있어야 하는 필드에 숫자가 하나도 없음", text))
            elif _HANGUL.search(text):
                out.append(Violation(key, f["name"], "numeric",
                                     "숫자 필드에 한글 설명이 섞여 있음", text))

        if rule.get("tag") and parse_tag(text) is None:
            # 명명 규칙은 Area(문자+숫자) - 설비종류 - 일련번호. 판정은 preprocess 몫
            out.append(Violation(key, f["name"], "tag",
                                 "태그 명명 규칙(Area-설비종류-일련번호)에 맞지 않음", text))

        pat = rule.get("pattern")
        if pat and not re.fullmatch(pat, text):
            out.append(Violation(key, f["name"], "pattern",
                                 f"형식 불일치 (기대: {pat})", text))

        cap = rule.get("max_length")
        if cap and len(text) > int(cap):
            out.append(Violation(key, f["name"], "max_length",
                                 f"{cap}자를 넘음 ({len(text)}자)", text))

    return out


def check_value(field_key: str, value: object,
                fields: list[dict] | None = None,
                rules: dict[str, dict] | None = None) -> list[Violation]:
    """필드 하나만 검사한다. 파이프라인은 필드별로 판정을 요구한다.

    `validate_format` 은 값 묶음 전체를 보므로 다른 필드의 required 위반까지
    같이 나온다. 여기서는 이 필드에 해당하는 것만 돌려준다.
    """
    fields = fields if fields is not None else load_fields()
    one = [f for f in fields if f["key"] == field_key]
    if not one:
        return [Violation(field_key, "?", "unknown_field",
                          "schema/fields.yaml 에 없는 field_key", _s(value))]
    return validate_format({field_key: value}, fields=one, rules=rules)


def _s(v: object) -> str | None:
    return None if v is None else str(v)
