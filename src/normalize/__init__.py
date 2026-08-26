# -*- coding: utf-8 -*-
"""④ NORMALIZE — 표준값으로 바꾸고, 문서에 없는 파생 필드를 규칙으로 채운다.

■ 무엇이 달라졌나
────────────────────────────────────────────────────────────────────
기존 `pipeline.DefaultNormalize` 는 `run(ex, f)` 만 받아 **다른 필드를 볼 수
없었다.** 그래서 문서에 글자로 적혀 있지 않은 `type_name` 을 채울 방법이 없었고
`no_evidence` → `NA` 로 확정됐다.

    실측(골든셋 30건) — type_name 13건이 NA 로 확정.
    그중 대부분은 골든셋 원문라벨이 "NA (Tag에서 FV를 보고 유추)" 였다.
    즉 **사람도 문서에서 읽은 게 아니라 태그를 보고 도출한 값**이다.

이 모듈은 `context`(앞서 확정된 필드 값들)를 함께 받아 그 도출을 수행한다.

■ fluid_state 는 도출하지 않는다 (2026-08-26 협의)
────────────────────────────────────────────────────────────────────
유체명만으로 상태를 확정할 수 없는 경우가 있다 — 같은 유체가 공정 조건에 따라
액체이기도 기체이기도 하고 이상(two-phase) 흐름도 있다. 골든셋 30건에서는 낱말
규칙(STEAM/GAS → GAS)이 전부 맞았지만 표본이 좁아서다. ③-b 가 문서에서 못
읽으면 채우지 않고 `NA` 로 둔다.

■ 왜 파서(③)가 아니라 여기서 하나
────────────────────────────────────────────────────────────────────
CLAUDE.md 철학 4 — "근거 없는 값을 만들지 않는다. 모르면 state=NA + note."
파서가 문서에 없는 값을 지어내면 그 원칙이 깨진다. 파서는 계속 `null` 을 내고,
**규칙으로 채우는 일은 하류가 맡는다.** `schema.model_to_manufacturer` 가 이미
같은 선례다 — 문서에 없는 제조사를 모델명으로 채우고 근거를 trace 에 남긴다.

■ 도출값을 자동확정하지 않는다
────────────────────────────────────────────────────────────────────
도출값은 **문서 근거가 없다.** 확신도를 올리지 않으므로 `_decide()` 의
`confidence < threshold` 에 걸려 REVIEW 로 간다. `DualParser._merge()` 가
텍스트 단독 값을 다루는 방식과 같다.

    실측 근거 — 태그만으로는 확정되지 않는다.
      11-LV-001  은 LV 인데 골든셋 정답이 Flow Control Valve
      22-PCV-013 은 PCV 인데 정답이 Direct Operated Regulator
"""
from __future__ import annotations

import re                                              # 태그에서 설비종류를 뽑는 데 쓴다
from typing import Any                                 # context 타입 표기

from src import schema                                 # 규칙은 yaml 에서 읽는다 (철학 2)
from src.contracts import RawExtraction                # 계약을 복사하지 않고 import


# 태그에서 설비종류를 뽑는 패턴 — 끝의 "-FV-002" / "FV-18" 형태를 본다.
# preprocess.parse_tag 를 쓰지 않는 이유: 그 함수는 정규화 키를 만들고
# 설비종류만 따로 돌려주지 않는다. 여기서 필요한 것은 종류 두세 글자뿐이다.
_TAG_KIND = re.compile(r"([A-Z]{1,4})\s*-?\s*\d+[A-Z]?\s*$")


class Normalizer:
    """`NormalizeModule` 구현. 기존 동작을 유지하고 도출을 얹는다."""

    def run(self, ex: RawExtraction, f: Any,
            context: dict[str, str | None] | None = None) -> tuple[str | None, list[str]]:
        """표준값과 변환 이력을 돌려준다.

        입력  : ex — 파서 결과, f — 필드 정의, context — 앞서 확정된 필드 값들
        출력  : (표준값 또는 None, transform_trace)
        부수효과: 없음
        """
        trace: list[str] = []

        # ── 값이 있으면 기존 정규화 경로 ────────────────────────
        raw = (ex.raw_value or "").strip()               # 파서가 읽은 원문
        if raw:
            rule = schema.domain_rule(f.key)             # ATO → Fail Close 같은 도메인 규칙
            if rule:
                for item in schema.value_aliases(f.key) or []:
                    for src in (item.get("from") or []):
                        if src.upper() == raw.upper():   # 표기 매핑이 있으면 적용
                            trace.append(f"원문 '{raw}' · 규칙 {src} → {item.get('to')}")
                            return item.get("to"), trace
            if not schema.feature_enabled("unit_conversion"):
                trace.append("단위 변환 비활성 (MVP) — 원문 보존")
            return raw, trace                            # MVP 는 원문 표기 보존

        # ── 값이 없다 — 도출 규칙이 있으면 채운다 ───────────────
        rule = schema.derivation_for(f.key)              # rules.yaml 의 derived_fields
        if rule is None or not context:
            return None, trace                           # 규칙이 없으면 지금과 동일하게 빈 값

        src_key = rule.get("from")                       # 재료가 될 필드
        src_val = (context.get(src_key) or "").strip()   # 앞선 필드에서 확정된 값
        if not src_val:
            trace.append(f"도출 불가 — {src_key} 값이 없음")
            return None, trace

        how = rule.get("how")                            # 도출 방식
        if how == "tag_kind":
            out = self._from_tag_kind(src_val, rule)
        else:
            trace.append(f"도출 방식 '{how}' 미구현")
            return None, trace

        if out is None:
            trace.append(f"도출 실패 — {src_key}='{src_val}' 에 맞는 규칙 없음")
            return None, trace

        # 근거를 반드시 남긴다 — 문서에 없는 값이므로 사람이 되짚을 수 있어야 한다(철학 4)
        trace.append(f"{src_key}='{src_val}' → {f.key}='{out}' (문서 근거 없음 · 규칙 도출)")
        return out, trace

    # ── 도출 방식 ──────────────────────────────────────────
    @staticmethod
    def _from_tag_kind(tag: str, rule: dict) -> str | None:
        """태그의 설비종류로 값을 찾는다. 예: 10-FV-002 → FV → Flow Control Valve."""
        m = _TAG_KIND.search(tag.upper().replace(" ", ""))   # 끝에서 종류+번호를 찾는다
        if not m:
            return None
        return (rule.get("map") or {}).get(m.group(1))       # 매핑에 없으면 None


__all__ = ["Normalizer"]
