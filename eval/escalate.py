# -*- coding: utf-8 -*-
"""승격 판단 — 어느 필드를 상위 모델에 다시 물을 것인가

    from eval.escalate import reasons

    why = reasons(field, ex, file_tag)
    if why: ...   # gpt-5.6-terra 로 크롭 재판독

재시도가 아니라 **독립 2차 의견**이다
─────────────────────────────────────────────────────────────
`CLAUDE.md` 는 제약 위반에 재시도를 걸지 말라고 못박는다 — "값이 틀렸다,
다시 봐라" 는 실질적으로 *다른 답을 내놓으라* 는 지시이고, 문서에 진짜로 그
값이 있으면 모델은 순응해서 만들어낸다. 기준정보 시스템에서 최악의 실패다.

그래서 여기서 하는 것은 **같은 질문을 다른 조건으로 한 번 더** 묻는 것이다.

    다른 것    모델(상위) · 입력(bbox 크롭 → 실질 해상도 상승)
    같은 것    질문 문구. "이 영역에 문자 그대로 무엇이 적혀 있는지 보고하라"
    금지       "틀렸다" · "다시 확인하라" · 기대값 제시

그리고 **두 판독이 다르면 값을 고르지 않는다.** 확인필요로 사람에게 넘긴다.
"맞는 답이 나올 때까지 다시 묻기" 가 되면 위의 실패로 돌아간다.

무엇이 승격을 촉발하는가
─────────────────────────────────────────────────────────────
① 확신도가 필드 임계 미달        임계는 `fields.yaml` 에서 온다
② 규칙으로 검출된 불일치         VLM 태그 ≠ 파일명 태그
③ 안전·식별 필드는 항상          2개뿐이라 비용이 작고, 틀리면 가장 비싸다

②가 핵심이다. 2026-08-24 실측에서 태그가 `10-FV-002` → `10-ED-002` 로
오독되었는데, 파일명이 `A10FV002` 를 주므로 정규화 키가 어긋나 **자동으로
검출된다.** 사람이 보기 전에 기계가 아는 유일한 오독이다.
"""
from __future__ import annotations

from src import preprocess


def tag_mismatch(ex, file_tag: str | None) -> str:
    """VLM 이 읽은 태그가 파일명 태그와 다른가. → 사유 또는 빈 문자열.

    비교는 정규화 키로 한다 — `10-FV-002` 와 `10FV002` 는 같고
    `10-ED-002` 는 다르다. 파일명에 태그가 없으면 판단하지 않는다.
    """
    if not file_tag or not ex.raw_value:
        return ""
    want = preprocess.normalize_tag(file_tag)
    got = preprocess.normalize_tag(ex.raw_value)
    if not want or not got:
        return ""
    if want == got:
        return ""
    # 다중 태그 페이지 — 목록 안에 있으면 불일치가 아니다
    if want in preprocess.find_tags(ex.raw_value):
        return ""
    return f"파일명 태그({want})와 읽은 값({got})이 다르다"


def reasons(f, ex, file_tag: str | None = None) -> list[str]:
    """이 필드를 상위 모델에 다시 물어야 하는 사유. 없으면 빈 목록."""
    out = []
    if not ex.found:
        return out                      # 값이 없으면 크롭할 좌표도 없다
    if f.needs_human:
        out.append(f"안전·식별 필드({f.safety})")
    if ex.confidence < f.threshold:
        out.append(f"확신도 {ex.confidence:.2f} < 임계 {f.threshold:.2f}")
    if f.key == "engineering_tag_no":
        m = tag_mismatch(ex, file_tag)
        if m:
            out.append(m)
    return out


def settle(f, first, second) -> tuple[str | None, str, str]:
    """두 판독을 놓고 값을 정한다. → (값, 판정, 사유)

    판정
        agree     두 판독이 같다 — 확신을 높인다
        changed   달라서 2차(상위 모델·크롭)를 택했다. **확인필요로 남긴다**
        kept      2차가 못 읽었다 — 1차를 유지하고 확인필요

    2차를 택하는 근거는 입력이 더 좋기 때문이다(크롭 = 실질 해상도 상승).
    다만 어느 쪽이 맞는지 기계는 모르므로 **자동확정하지 않는다.**
    """
    from eval import compare
    if second is None or not second.found:
        return first.raw_value, "kept", "2차 판독이 값을 내지 못했다 — 1차 유지"
    if compare.same(first.raw_value, second.raw_value):
        return first.raw_value, "agree", "두 판독이 일치"
    return (second.raw_value, "changed",
            f"1차 {first.raw_value!r} vs 2차 {second.raw_value!r} — "
            f"2차(크롭·상위 모델)를 택하고 사람 확인 필요")
