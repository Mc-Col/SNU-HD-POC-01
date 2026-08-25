# -*- coding: utf-8 -*-
"""스키마 파일끼리 서로를 가리키는 곳이 어긋나지 않는가.

■ 왜 이 테스트가 있나
────────────────────────────────────────────────────────────────────
`schema/` 는 파일 세 개가 서로를 참조한다.

    fields.yaml     필드 28개 — 이름·유사표현·필수 여부
    rules.yaml      구역 사전 · 복합 라벨 · 형식 규칙 — **field key 로 필드를 가리킨다**
    guidance.yaml   사람이 읽을 판단 지침 — 역시 field key 로 가리킨다

필드 표준은 라벨링을 거치며 바뀐다(30 → 28: `POSITIONER TYPE` 삭제,
`RATED CV MAX` 병합). 그때 **가리키는 쪽이 같이 안 바뀌면 조용히 죽는다.**
파일은 멀쩡히 파싱되고, 테스트도 안 깨지고, 화면에도 아무 일이 없다.
그냥 그 지침이 영영 안 보일 뿐이다.

2026-08-25 에 실제로 두 번 겪었다.
  · `guidance.yaml` 의 `rated_cv_max` · `rated_cv_normal` · `positioner_type`
    → 세 지침이 삭제·병합된 옛 키를 가리키고 있어 화면에 뜨지 않았다
  · `rules.yaml` 의 구역 허용 목록에 `characteristic_trim_form`
    → 실제 key 는 `characteristic` 이라 그 필드가 구역에서 영영 걸러졌다

둘 다 사람이 눈으로 찾았다. 이 테스트가 있으면 기계가 찾는다.
"""
import os
import sys

import pytest
import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

SCHEMA = os.path.join(ROOT, "schema")


def _load(name: str) -> dict:
    with open(os.path.join(SCHEMA, name), encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@pytest.fixture(scope="module")
def keys() -> set[str]:
    return {f["key"] for f in _load("fields.yaml")["fields"]}


@pytest.fixture(scope="module")
def rules() -> dict:
    return _load("rules.yaml")


def test_형식_규칙이_없는_필드를_가리키지_않는다(keys, rules):
    bad = sorted(set(rules.get("format_rules") or {}) - keys)
    assert not bad, f"format_rules 가 없는 필드를 가리킨다: {bad}"


def test_구역_허용_목록이_없는_필드를_가리키지_않는다(keys, rules):
    listed = {k for v in (rules.get("sections") or {}).get("fields", {}).values() for k in (v or [])}
    bad = sorted(listed - keys)
    assert not bad, f"sections.fields 가 없는 필드를 가리킨다: {bad}"


def test_구역_표기가_정의된_표준_구역만_쓴다(rules):
    sec = rules.get("sections") or {}
    used = set((sec.get("aliases") or {}).values())
    defined = set(sec.get("fields") or {})
    assert not used - defined, f"허용 목록이 없는 표준 구역: {sorted(used - defined)}"


def test_복합_라벨이_없는_필드를_가리키지_않는다(keys, rules):
    listed = {k for rule in (rules.get("composite_labels") or [])
              for k in (rule.get("fields") or []) if k}      # null 은 '대응 필드 없음'
    bad = sorted(listed - keys)
    assert not bad, f"composite_labels 가 없는 필드를 가리킨다: {bad}"


def test_표기_매핑이_없는_필드를_가리키지_않는다(keys, rules):
    bad = sorted(set(rules.get("value_aliases") or {}) - keys)
    assert not bad, f"value_aliases 가 없는 필드를 가리킨다: {bad}"


def test_판단_지침이_없는_필드를_가리키지_않는다(keys):
    """지침은 화면이 그대로 보여준다. 키가 어긋나면 조용히 안 뜬다."""
    bad = sorted(set((_load("guidance.yaml").get("fields") or {})) - keys)
    assert not bad, f"guidance 가 없는 필드를 가리킨다: {bad}"


def test_MVP_필드는_모두_판단_지침을_갖는다(keys):
    """예외가 뜨면 사람이 판단해야 하는데, MVP 필드에 지침이 없으면 매번 다시 판단하게 된다."""
    mvp = [f["key"] for f in _load("fields.yaml")["fields"] if f.get("mvp")]
    have = set((_load("guidance.yaml").get("fields") or {}))
    assert not [k for k in mvp if k not in have]


def test_유사표현이_다른_필드의_표준명과_겹치지_않는다():
    """표준명을 빌려 쓰는 것은 허용하되(구역이 가른다), 이름 주인이 이겨야 한다.

    `FieldIndex.lookup` 이 그 규칙을 지키는지는 파서 쪽 테스트가 본다.
    여기서는 **의도한 것만** 겹치는지, 즉 겹침이 늘어나지 않았는지를 본다.
    """
    fields = _load("fields.yaml")["fields"]
    from src.parsers.text.field_index import normalize_label as nz
    names = {nz(f["name"]): f["key"] for f in fields}
    overlap = sorted({(names[nz(a)], f["key"]) for f in fields
                      for a in (f.get("aliases") or [])
                      if nz(a) in names and names[nz(a)] != f["key"]})
    assert overlap == [("model_no", "positioner_model_no")], overlap
