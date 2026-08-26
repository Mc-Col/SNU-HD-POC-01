# -*- coding: utf-8 -*-
"""⑤-b 도메인 검증 — **검증기 조립은 여기 한 곳에서만 한다**

    from src.validate.domain import check_all

    flags = check_all(field, value, ex, context, confidence=ex.confidence)
    # → [Flag(source='어휘', why='...'), ...]

왜 조립 함수가 필요한가
─────────────────────────────────────────────────────────────
검증 수단이 셋이고 전부 코드다 — 확신도 · 허용 어휘 · 출처 교차.
**화면과 평가 하네스가 각자 조립하면 반드시 갈린다.** 순서가 다르거나 한쪽만
새 검사를 추가하면, 그 순간 *"화면에서 본 것과 채점된 숫자가 다르다"* 가 되고
그건 측정 전체를 못 믿게 만든다.

그래서 **조합을 아는 곳을 하나로 못박는다.** 화면도 하네스도 이 함수만 부른다.

표시는 계산되는 것이지 저장되는 것이 아니다
─────────────────────────────────────────────────────────────
`Flag` 를 `FieldRecord` 에 넣지 않는다. 표시는 **(값 · 추출 · 문맥 · 규칙)에서
매번 다시 계산되는 파생물**이고, 저장하면 규칙이 바뀔 때 낡은 채로 남는다.
`eval/store.py` 가 정규화된 값을 저장하지 않는 것과 같은 이유다 —
**규칙에서 다시 계산되는 것을 저장하면 규칙과 어긋나고, 어긋난 줄 아무도 모른다.**

감사 시트에 남기려면 내보내는 시점에 이 함수를 다시 부르면 된다.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.contracts import FailureKind
from src.validate.domain import provenance, vocabulary

__all__ = ["Flag", "check_all", "vocabulary", "provenance"]


@dataclass(frozen=True)
class Flag:
    """확인필요 표시 하나. **여러 개가 동시에 붙을 수 있다.**

    `FieldRecord.failure` 는 하나뿐이라 두 이유가 겹치면 하나가 지워진다.
    그래서 목록으로 돌려준다 — 사람이 무엇을 확인해야 하는지는 이유마다 다르다.
    """
    source: str          # 확신도 · 어휘 · 출처
    why: str
    kind: FailureKind


def check_all(f, value, ex=None, context=None,
              confidence: float | None = None) -> list[Flag]:
    """이 값에 붙는 확인필요 표시 전부. 없으면 빈 목록.

    f           `schema.Field`
    value       ④ Normalize 를 거친 값
    ex          `RawExtraction` — 출처 검증에 쓴다
    context     같은 문서의 {field_key: RawExtraction} — 교차검증에 쓴다
    confidence  없으면 `ex.confidence` 를 쓴다

    순서는 **싼 것부터**다. 셋 다 코드이고 비용이 0원이라 지금은 의미가
    없지만, 유료 검사(평가 에이전트)가 붙으면 이 순서가 비용 사다리가 된다.
    """
    out: list[Flag] = []
    if value is None or not str(value).strip():
        return out

    conf = confidence if confidence is not None else getattr(ex, "confidence", None)
    if conf is not None and conf < f.threshold:
        out.append(Flag("확신도", f"확신도 {conf:.2f} < 임계 {f.threshold:.2f}",
                        FailureKind.CONSTRAINT))

    kind, why = vocabulary.check(f, value, ex, context)
    if kind is not FailureKind.NONE:
        out.append(Flag("어휘", why, kind))

    kind, why = provenance.check(f, value, ex, context)
    if kind is not FailureKind.NONE:
        out.append(Flag("출처", why, kind))

    return out


def observe_all(f, value, doc_id: str = "", label: str = "") -> None:
    """어휘 밖 값을 후보 큐에 쌓는다. `check_all` 과 짝이다.

    큐에 쌓는 것은 **판정과 분리**한다 — 화면은 판정만 필요하고, 큐는
    실행 단위로 모아 사람에게 한 번에 보여주는 것이라 생명주기가 다르다.
    """
    vocabulary.observe(f.key, str(value), doc_id, label=label)
