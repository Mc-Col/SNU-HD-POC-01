# -*- coding: utf-8 -*-
"""⑤-b 허용 어휘 검증 — 표에 없는 값을 사람에게 보낸다

    from src.validate.domain import vocabulary

    kind, why = vocabulary.check(field, value)      # 어휘 밖인가
    vocabulary.observe(field, value, doc_id, note)  # 후보 큐에 쌓는다
    rows = vocabulary.candidates()                  # 사람이 승인할 목록

왜 이 단계가 필요한가
─────────────────────────────────────────────────────────────
2026-08-25 실측 — 틀린 값 7칸 중 **표시가 붙은 것은 1칸**이었다(재현율 14%).
나머지 6칸은 **아무 표시 없이 마스터DB 로 갈 수 있었다.** 이 과제가 없애려는
문제가 정확히 그것이다.

확신도로는 못 잡는다. 모델이 `C5` 를 `CS` 로 읽을 때 **확신을 갖고 틀린다.**
그런데 `CS` 는 이 코퍼스의 재질 어휘에 없다 — 어휘 대조는 그것을 잡는다.
그리고 **비용이 0원이고 항상 같은 결과**다(개발 철학 6).

바꾸지 않고 표시만 한다
─────────────────────────────────────────────────────────────
`CS` 를 `C5` 로 **고치지 않는다.** 둘은 한 글자 차이지만 탄소강과 Cr-Mo 합금강
이고, 사용 온도 한계가 다르다. 어느 쪽이 맞는지는 문서를 본 사람만 안다.

값을 바꾸는 것은 `value_aliases` 에 표기 변종으로 등재된 경우뿐이고, 그것은
④ Normalize 의 몫이다. 여기서는 **판정만** 한다.

후보 큐 — Loop C 의 입구
─────────────────────────────────────────────────────────────
어휘 밖 값을 근거와 함께 쌓아 두고 **사람이 승인한 것만** 규칙 파일로 간다.
기계가 자동으로 규칙을 쓰면 모델의 오독이 영구 규칙이 되고, 규칙 파일이
"도메인 전문가가 읽고 고치는 산출물" 이라는 성질을 잃는다.

큐는 실행 단위로 모았다가 **한 번에** 보여준다. 문서마다 물으면 사람이
읽지 않고 승인하게 되고, 그러면 승인이라는 안전장치가 형식만 남는다.
빈도순으로 정렬하는 이유도 같다 — 40건에서 나온 표현은 볼 가치가 있고
1건짜리는 스캔 잡티일 수 있다.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field as dc_field

from src import schema
from src.contracts import FailureKind


@dataclass
class Candidate:
    """어휘 밖에서 관측된 값 하나. 승인 판단에 필요한 근거를 함께 담는다."""
    field_key: str
    value: str
    count: int = 0
    docs: list[str] = dc_field(default_factory=list)
    labels: list[str] = dc_field(default_factory=list)   # 원문 항목명
    note: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return (self.field_key, schema.norm_label(self.value))


_QUEUE: dict[tuple[str, str], Candidate] = {}


# ── 판정 ────────────────────────────────────────────────────────

def check(f, value, ex=None, context=None) -> tuple[FailureKind, str]:
    """어휘 밖인가. → (실패유형, 사유). 계약은 다른 Validate 모듈과 같다.

    판정하지 않는 경우
        어휘가 정의되지 않은 필드   모델번호·태그·수치 등
        값이 비었거나 N/A          ③④ 가 이미 처리했다
    """
    if value is None or not str(value).strip():
        return FailureKind.NONE, ""
    ok = schema.in_vocabulary(f.key, str(value))
    if ok is None or ok:
        return FailureKind.NONE, ""

    vocab = schema.allowed_values(f.key)
    near = _nearest(str(value), vocab)
    tail = f" 가장 가까운 허용값은 {near!r} 이다." if near else ""
    return (FailureKind.FORMAT,
            f"허용 어휘에 없는 값이다 — {value!r}.{tail} "
            f"값을 바꾸지 않고 사람 확인으로 넘긴다")


def _nearest(value: str, vocab) -> str | None:
    """가장 가까운 허용값. **보정에 쓰지 않는다** — 사람에게 보여줄 힌트다.

    후보가 여럿이면 알려주지 않는다. `300#`·`600#`·`900#` 처럼 이웃이 붙어
    있는 어휘에서 하나를 고르면 그것이 곧 잘못된 유도다.
    """
    import difflib
    v = schema.norm_label(value)
    scored = sorted(
        ((difflib.SequenceMatcher(None, v, schema.norm_label(c)).ratio(), c)
         for c in vocab), reverse=True)
    if not scored or scored[0][0] < 0.7:
        return None
    if len(scored) > 1 and abs(scored[0][0] - scored[1][0]) < 1e-9:
        return None                      # 동점 — 고르지 않는다
    return scored[0][1]


# ── 후보 큐 ─────────────────────────────────────────────────────

def observe(field_key: str, value: str, doc_id: str = "",
            label: str = "", note: str = "") -> None:
    """어휘 밖 값을 후보로 기록한다. 같은 값은 합쳐서 센다."""
    if value is None or not str(value).strip():
        return
    if schema.in_vocabulary(field_key, str(value)) is not False:
        return                            # 어휘 안이거나 판정 대상 아님
    k = (field_key, schema.norm_label(value))
    c = _QUEUE.get(k)
    if c is None:
        c = _QUEUE[k] = Candidate(field_key=field_key, value=str(value).strip())
    c.count += 1
    if doc_id and doc_id not in c.docs:
        c.docs.append(doc_id)
    if label and label not in c.labels:
        c.labels.append(label)
    if note and not c.note:
        c.note = note


def candidates(min_count: int = 1) -> list[Candidate]:
    """승인 대기 목록. **빈도순** — 자주 나온 것부터 본다."""
    out = [c for c in _QUEUE.values() if c.count >= min_count]
    return sorted(out, key=lambda c: (-c.count, c.field_key, c.value))


def reset() -> None:
    """큐를 비운다. 실행 단위로 모으므로 실행 시작에 부른다."""
    _QUEUE.clear()


def as_rows(min_count: int = 1) -> list[dict]:
    """화면·리포트에 쓸 형태. 승인 UI 가 이 모양을 그대로 받는다."""
    rows = []
    for c in candidates(min_count):
        vocab = schema.allowed_values(c.field_key)
        rows.append({
            "field_key": c.field_key,
            "value": c.value,
            "count": c.count,
            "docs": ", ".join(c.docs[:6]) + ("…" if len(c.docs) > 6 else ""),
            "labels": " · ".join(c.labels[:3]),
            "nearest": _nearest(c.value, vocab) or "",
            "correctable": c.field_key in schema.enum_correct_fields(),
            "note": c.note,
        })
    return rows


def summary() -> dict:
    """한 줄 요약 — 리포트 헤드라인에 쓴다."""
    cs = candidates()
    return {
        "candidates": len(cs),
        "observations": sum(c.count for c in cs),
        "fields": len({c.field_key for c in cs}),
    }
