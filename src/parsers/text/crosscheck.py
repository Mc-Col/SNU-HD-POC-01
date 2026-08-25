# -*- coding: utf-8 -*-
"""③ 두 경로 대조 — VLM 이 정한 값을 텍스트가 보증하는가.

■ 무슨 작업인가
────────────────────────────────────────────────────────────────────
같은 페이지를 두 방법으로 읽는다.

    ③-b VLM 파서    이미지를 보고 **어느 값이 어느 필드인지** 정한다
    ③-a 텍스트 파서  글자 층에서 라벨과 값을 읽는다

CLAUDE.md 는 둘의 역할을 이렇게 갈라 놓았다.

    "텍스트 레이어가 있다고 VLM 을 건너뛰지 않는다.
     텍스트는 **글자를 보증할 뿐**이고 어느 값이 어느 필드인지는 VLM 이 정한다."

이 모듈이 그 문장을 코드로 옮긴 것이다. VLM 결과를 텍스트 결과와 맞춰 보고,
**다르면 사람에게 넘긴다.** 같으면 서로 독립인 두 경로가 같은 글자를 읽었다는
뜻이므로 그 값은 믿을 만하다.

■ 왜 텍스트를 VLM 프롬프트에 넣지 않는가 (2026-08-25 판단)
────────────────────────────────────────────────────────────────────
텍스트 결과를 VLM 에게 미리 보여주는 방법도 있었다. 하지만

  · 텍스트 파서도 틀린다. 실측에서 액추에이터 묶음의 `Model No.`(880)를 밸브
    모델로 집은 오답이 있었다. 그것을 후보로 주면 VLM 이 그대로 확정할 수 있고,
    **두 경로가 같은 오답에 합의하면 틀린 값이 자동확정으로 통과한다.**
  · 라벨링 킷 기입 안내가 사람에게 경고하는 것과 같은 문제다 —
    "AI 답을 먼저 보면 무비판 수락하게 되어(앵커링) 정확도가 실제보다 높게 나온다."
  · 섞으면 "독립 두 경로가 같은 값을 냈다" 는 측정을 잃는다.

그래서 **독립 실행 후 대조**로 간다. 단, VLM 이 못 읽은 필드(`N/A`)에 한해
재판독 근거로 텍스트를 주는 것은 별개이고 CLAUDE.md 의 재시도 원칙에도 맞는다
(재시도는 추출 실패에만). 그 판단은 이 모듈의 불일치·미추출 통계를 보고 정한다.

■ 값 비교를 다시 만들지 않는다
────────────────────────────────────────────────────────────────────
`eval/compare.same()` 을 쓴다. 단위 표기(`142.6 m3/Hr` vs `142.6`)·로마자
(`CLASS 4` vs `CLASS IV`)·대소문자 차이를 이미 흡수하고, `rules.yaml` 의
`numeric` 표시까지 반영된다. 여기서 따로 비교하면 채점기·평가 하네스·대조기가
서로 다른 답을 내게 된다.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from eval.compare import same as _same                    # noqa: E402
from src import schema as _schema                         # noqa: E402

# 판정 — 화면·로그에 그대로 쓴다
AGREE = "일치"            # 두 경로가 같은 값 → 신뢰 상승
NOTATION = "표기차이"      # 숫자는 같고 표기만 다름 → 표준형이 정해지면 사라진다
CONFLICT = "불일치"        # 다른 값 → 사람이 본다
VLM_ONLY = "VLM만"        # 텍스트가 그 페이지를 못 읽음 (스캔 혼재 등)
TEXT_ONLY = "텍스트만"     # VLM 이 놓침 → 후보로 제시
NEITHER = "없음"          # 둘 다 못 읽음 → N/A 경로


def standardize(field_key: str, value: object) -> str:
    """`rules.yaml` 의 표기 매핑을 적용한 값.

    대조는 **④ Normalize 를 지난 값끼리** 하는 것이 원칙이다(파이프라인의 ⑤
    Validate 단계는 ④ 뒤에 온다). 다만 원문끼리 비교해야 하는 상황도 있어,
    여기서도 사전을 한 번 적용한다 — `VALVE CLOSE` 와 `FAIL CLOSE` 를 다른 값으로
    부르면 사람을 불필요하게 부른다.
    """
    raw = str(value or "").strip()
    probe = _schema.norm_label(raw)
    for m in _schema.value_aliases(field_key):
        if probe in {_schema.norm_label(c) for c in m.get("from", [])}:
            return str(m["to"])
    return raw


def numeric_flag(field_key: str) -> bool | None:
    """이 필드를 숫자로 대조할지. `rules.yaml` 이 말해주지 않으면 자동 판정.

    `value_aliases[field].numeric` 을 읽는다 — `eval/harness.py` 의 `_numeric()`
    과 같은 자리다. 내 모듈들(채점기·대조기)은 이 함수 하나만 쓴다.
    """
    for m in _schema.value_aliases(field_key):
        if m.get("numeric") is not None:
            return bool(m["numeric"])
    return None


@dataclass(frozen=True)
class Agreement:
    """필드 하나에 대한 대조 결과."""
    field_key: str
    state: str
    vlm: str | None = None
    text: str | None = None
    note: str = ""

    @property
    def needs_human(self) -> bool:
        """사람이 봐야 하는가. 불일치만 해당한다.

        `텍스트만` 은 갈등이 아니라 **후보 제시**다. 값을 채울지는 하류(⑥ State)가
        정할 일이고, 여기서 사람을 부르면 후보가 많은 문서마다 화면이 막힌다.
        """
        return self.state == CONFLICT

    def as_note(self) -> str:
        if self.state == AGREE:
            return "두 경로(VLM·텍스트)가 같은 값을 읽음"
        if self.state == NOTATION:
            return (f"숫자는 같고 표기만 다름 — VLM {self.vlm!r} vs 텍스트 {self.text!r} "
                    f"(표준형 미정)")
        if self.state == CONFLICT:
            return f"두 경로가 다름 — VLM {self.vlm!r} vs 텍스트 {self.text!r}"
        if self.state == TEXT_ONLY:
            return f"텍스트에서만 읽힘 — 후보 {self.text!r}"
        if self.state == VLM_ONLY:
            return "VLM 만 읽음 (텍스트 레이어에 없음)"
        return "두 경로 모두 값이 없음"


EMPTY_TOKENS = {"N/A", "NA", "-", "판독불가"}


def _pairs(items: Iterable[Any]) -> list[tuple[str, Any]]:
    """RawExtraction 목록 또는 {key: value} → (key, 원본값) 목록."""
    if isinstance(items, dict):
        return [(k, v) for k, v in items.items() if k]
    return [(getattr(x, "field_key", None), getattr(x, "raw_value", None))
            for x in (items or []) if getattr(x, "field_key", None)]


def _values(items: Iterable[Any]) -> dict[str, str]:
    """{key: 값}. 값 없음(None · 빈칸 · N/A · 판독불가)은 담지 않는다."""
    out: dict[str, str] = {}
    for key, val in _pairs(items):
        if val is None:
            continue
        text = str(val).strip()
        if text and text.upper() not in EMPTY_TOKENS:
            out[key] = text
    return out


def crosscheck(vlm: Iterable[Any], text: Iterable[Any],
               fields: Sequence[str] | None = None) -> list[Agreement]:
    """두 파서 결과를 필드별로 대조한다.

    vlm / text
        `RawExtraction` 목록 (파이프라인이 주는 형태) 또는 `{field_key: 값}`.
    fields
        볼 필드 목록. 주지 않으면 두 쪽에 나온 필드 전부를 본다.
        (파이프라인은 `target_fields()` 를 그대로 넘기면 된다.)
    """
    v, t = _values(vlm), _values(text)
    if fields is not None:
        keys = list(fields)
    else:
        # 값이 비어 있어도 **온 필드는 결과에 남긴다.** 조용히 사라지면
        # "둘 다 못 읽음" 과 "애초에 안 왔음" 을 구분할 수 없다 (철학 5).
        keys = sorted({k for k, _ in _pairs(vlm)} | {k for k, _ in _pairs(text)})

    out: list[Agreement] = []
    for key in keys:
        a, b = v.get(key), t.get(key)
        if a is None and b is None:
            out.append(Agreement(key, NEITHER))
        elif b is None:
            out.append(Agreement(key, VLM_ONLY, vlm=a))
        elif a is None:
            out.append(Agreement(key, TEXT_ONLY, text=b))
        elif (_same(a, b, numeric_flag(key))
              or _same(standardize(key, a), standardize(key, b), numeric_flag(key))):
            out.append(Agreement(key, AGREE, vlm=a, text=b))
        elif _same(a, b, True):
            # 숫자는 같고 글자만 다르다 — 등급이 대표적이다
            #   `300#` vs `ANSI CLASS 300` · `1500#` vs `CL 1500`
            # 같은 값이므로 사람을 부르지 않는다. 다만 **표준형이 아직 정해지지
            # 않았다는 사실**은 드러내야 해서 별도 판정으로 둔다 —
            # `rules.yaml` 의 표기 매핑이 채워지면 이 판정은 사라진다.
            out.append(Agreement(key, NOTATION, vlm=a, text=b))
        else:
            out.append(Agreement(key, CONFLICT, vlm=a, text=b))
    return out


def summary(results: Iterable[Agreement]) -> dict[str, int]:
    """판정별 개수. 로그·발표 숫자로 쓴다."""
    out = {AGREE: 0, NOTATION: 0, CONFLICT: 0, VLM_ONLY: 0, TEXT_ONLY: 0, NEITHER: 0}
    for r in results:
        out[r.state] += 1
    return out


def agreement_rate(results: Iterable[Agreement]) -> float | None:
    """두 경로가 다 읽은 칸 중 일치 비율. 대조 가능한 칸이 없으면 None.

    이것이 "글자가 보증되었다" 의 측정치다. 분모에 한쪽만 읽은 칸을 넣으면
    텍스트가 약한 문서에서 비율이 임의로 오르내린다.
    """
    got = [r for r in results if r.state in (AGREE, NOTATION, CONFLICT)]
    if not got:
        return None
    return sum(1 for r in got if r.state in (AGREE, NOTATION)) / len(got)
