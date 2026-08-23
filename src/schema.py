# -*- coding: utf-8 -*-
"""
스키마 로더 — schema/fields.yaml · rules.yaml 접근 창구 (공용)

모든 모듈이 이 파일을 통해 필드 정의와 규칙을 읽는다.
규칙을 코드에 하드코딩하지 않기 위한 유일한 통로.

    from src import schema

    for f in schema.mvp_fields():
        print(f.key, f.name, f.aliases)

    schema.threshold("engineering_tag_no")   # 1.0
    schema.safety("actuator_fail_action")    # "safety"
    schema.domain_rule("actuator_fail_action")
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field as _field
from functools import lru_cache
from typing import Any

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIELDS_PATH = os.path.join(ROOT, "schema", "fields.yaml")
RULES_PATH = os.path.join(ROOT, "schema", "rules.yaml")
GUIDANCE_PATH = os.path.join(ROOT, "schema", "guidance.yaml")


@dataclass(frozen=True)
class Field:
    key: str
    name: str
    group: str
    db_code: str
    desc: str
    required: bool
    safety: str          # safety / identity / normal
    source: str          # document / derived / system
    mvp: bool
    threshold: float
    aliases: tuple[str, ...] = ()
    example: str = ""

    @property
    def needs_human(self) -> bool:
        """자동확정이어도 사람 확인이 필요한 필드인가."""
        return self.safety in ("safety", "identity")

    @property
    def match_terms(self) -> tuple[str, ...]:
        """헤더 매핑에 사용할 후보 문자열 (표준명 + 유사표현)."""
        return (self.name,) + tuple(self.aliases)


# ── 로딩 ──────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _fields_doc() -> dict[str, Any]:
    with open(FIELDS_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


@lru_cache(maxsize=1)
def _rules_doc() -> dict[str, Any]:
    with open(RULES_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@lru_cache(maxsize=1)
def _guidance_doc() -> dict[str, Any]:
    try:
        with open(GUIDANCE_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


@lru_cache(maxsize=1)
def all_fields() -> tuple[Field, ...]:
    out = []
    for d in _fields_doc().get("fields", []):
        out.append(Field(
            key=d["key"], name=d["name"], group=d.get("group", ""),
            db_code=d.get("db_code", ""), desc=d.get("desc", ""),
            required=bool(d.get("required", False)),
            safety=d.get("safety", "normal"),
            source=d.get("source", "document"),
            mvp=bool(d.get("mvp", False)),
            threshold=float(d.get("threshold", 0.90)),
            aliases=tuple(d.get("aliases") or ()),
            example=d.get("example", ""),
        ))
    return tuple(out)


@lru_cache(maxsize=1)
def _by_key() -> dict[str, Field]:
    return {f.key: f for f in all_fields()}


def get(key: str) -> Field:
    try:
        return _by_key()[key]
    except KeyError:
        raise KeyError(
            f"schema/fields.yaml 에 '{key}' 필드가 없습니다. "
            f"키 목록: {', '.join(sorted(_by_key())[:8])} ..."
        ) from None


def mvp_fields() -> tuple[Field, ...]:
    return tuple(f for f in all_fields() if f.mvp)


def required_fields() -> tuple[Field, ...]:
    return tuple(f for f in all_fields() if f.required)


def safety_fields() -> tuple[Field, ...]:
    return tuple(f for f in all_fields() if f.needs_human)


def threshold(key: str) -> float:
    return get(key).threshold


def safety(key: str) -> str:
    return get(key).safety


def aliases(key: str) -> tuple[str, ...]:
    return get(key).aliases


def alias_index(only_mvp: bool = False) -> dict[str, str]:
    """정규화한 표기 → field_key. Text Parser 의 헤더 매핑용.

    같은 표기가 두 필드에 중복되면 먼저 정의된 필드가 이긴다(경고 없음 —
    사전 정리는 schema/fields.yaml 에서 해야 한다).
    """
    idx: dict[str, str] = {}
    for f in all_fields():
        if only_mvp and not f.mvp:
            continue
        for term in f.match_terms:
            idx.setdefault(norm_label(term), f.key)
    return idx


def norm_label(s: str) -> str:
    """라벨 비교용 정규화 — 대소문자·공백·구두점 제거."""
    keep = [c for c in str(s).upper() if c.isalnum()]
    return "".join(keep)


# ── 규칙 ──────────────────────────────────────────────────────

def domain_rule(field_key: str) -> dict[str, Any] | None:
    return (_rules_doc().get("domain_rules") or {}).get(field_key)


def value_aliases(field_key: str) -> list[dict[str, Any]]:
    return (_rules_doc().get("value_aliases") or {}).get(field_key) or []


# ── 판단 지침 (자연어 규칙) ───────────────────────────────────
#
# 결정론적으로 적을 수 없는 판단 기준을 사람 말로 둔다. 화면이 예외 항목에서
# 그대로 보여주고, 화면에서 편집도 된다. rules.yaml 과 분리한 이유는
# 그 파일이 주석 많은 손 관리 파일이어서 기계가 덮어쓰면 안 되기 때문이다.

GUIDANCE_HEADER = """# 판단 지침 — 자연어 규칙
#
# 소유: 이종수 책임 · 서경빈 선임 (도메인 전문가 공동)
#
# rules.yaml 은 기계가 적용하는 결정론적 규칙이다. 이 파일은 그것으로 적을 수 없는
# 판단 기준을 사람 말로 적는 곳이다. 예외 항목(확인필요·N/A)이 뜨면 화면이 이 글을
# 그대로 보여준다 — 검토자가 매번 같은 판단을 다시 발명하지 않게 하는 것이 목적이다.
#
# 화면에서도 편집된다(HITL 의 [지침] 버튼). 그래서 rules.yaml 과 분리했다 —
# rules.yaml 은 주석이 많은 손 관리 파일이고, 기계가 덮어쓰면 주석이 사라진다.
#
# 손으로 편집해도 되고, 화면에서 적어도 된다. 형식만 지키면 된다:
#   fields:
#     <field_key>:
#       text: |
#         여러 줄로 자유롭게
#       by: "작성자"
#       updated: "YYYY-MM-DD"

"""


def guidance(field_key: str) -> dict[str, Any] | None:
    """이 필드의 판단 지침. 없으면 None."""
    return ((_guidance_doc().get("fields") or {}).get(field_key)) or None


def general_guidance() -> dict[str, Any] | None:
    """필드와 무관한 공통 지침."""
    return _guidance_doc().get("general") or None


def all_guidance() -> dict[str, dict[str, Any]]:
    return dict(_guidance_doc().get("fields") or {})


def set_guidance(field_key: str, text: str, by: str = "", today: str = "") -> str | None:
    """지침을 쓰고 파일에 반영한다. 이전 내용을 돌려준다(로그용).

    `today` 를 넘기지 않으면 기록하지 않는다 — 시각 의존을 호출자에게 남긴다.
    """
    doc = dict(_guidance_doc())
    fields = dict(doc.get("fields") or {})
    before = (fields.get(field_key) or {}).get("text")

    text = (text or "").strip()
    if text:
        entry = {"text": text + "\n"}
        if by:
            entry["by"] = by
        if today:
            entry["updated"] = today
        fields[field_key] = entry
    else:
        fields.pop(field_key, None)          # 빈 글은 삭제로 취급

    doc["fields"] = fields
    body = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False,
                          default_flow_style=False, width=88)
    with open(GUIDANCE_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(GUIDANCE_HEADER + body)
    _guidance_doc.cache_clear()
    return before


def feature_enabled(name: str) -> bool:
    """단위 변환 등 To-be 기능의 on/off."""
    block = _rules_doc().get(name)
    return bool(block and block.get("enabled"))


# ── 재현성 ────────────────────────────────────────────────────

def config_hashes() -> dict[str, str]:
    """설정 파일의 내용 해시. on_config_load 에서 기록해 재현성을 확보한다."""
    out = {}
    for path in (FIELDS_PATH, RULES_PATH, GUIDANCE_PATH):
        rel = os.path.relpath(path, ROOT).replace("\\", "/")
        try:
            with open(path, "rb") as f:
                out[rel] = hashlib.sha256(f.read()).hexdigest()[:12]
        except FileNotFoundError:
            out[rel] = "MISSING"
    return out


def summary() -> dict[str, Any]:
    fs = all_fields()
    return {
        "field_count": len(fs),
        "required": sum(1 for f in fs if f.required),
        "mvp": sum(1 for f in fs if f.mvp),
        "safety": sum(1 for f in fs if f.safety == "safety"),
        "identity": sum(1 for f in fs if f.safety == "identity"),
        "with_aliases": sum(1 for f in fs if f.aliases),
        "hashes": config_hashes(),
    }


def reload() -> None:
    """yaml 을 수정한 뒤 캐시를 비운다 (개발 중 사용)."""
    for fn in (_fields_doc, _rules_doc, _guidance_doc, all_fields, _by_key):
        fn.cache_clear()


if __name__ == "__main__":
    import json, sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(summary(), ensure_ascii=False, indent=2))
    print("\nMVP 필드:")
    for f in mvp_fields():
        mark = f" [{f.safety}]" if f.needs_human else ""
        print(f"  {f.key:<26} {f.name}{mark}  임계 {f.threshold:.2f}  유사표현 {len(f.aliases)}개")
