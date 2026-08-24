# -*- coding: utf-8 -*-
"""실행 이력 — 개선을 측정할 수 있게 만드는 장치

    from eval.history import archive

    archive(res, report, stage="vlm", note="rating 별칭 추가")

왜 필요한가
─────────────────────────────────────────────────────────────
`runs/eval_report.md` 는 실행마다 덮어써진다. 그러면 **개선을 측정할 수 없다.**
"별칭을 넣었더니 68% → 81% 가 되었다" 를 말하려면 이전 숫자가 남아 있어야
한다. 그것이 Loop C 의 전부다 — 로그와 사람의 수정을 규칙으로 되돌리는 루프는
before/after 없이 성립하지 않는다.

남기는 것
    runs/eval/<시각>-<stage>.md     리포트 전문. 덮어쓰지 않는다
    docs/eval_history.md            한 줄 요약의 append-only 표

한 줄 요약에 무엇을 넣는가
    정확도 하나만 남기면 나중에 "무엇을 바꿨더니 올랐나" 를 못 짚는다.
    그래서 **판정 분포와 변경 메모를 함께** 남긴다. 특히 `근거없음오답` 은
    다른 숫자가 올라가도 이것이 늘면 개선이 아니므로 별 열로 둔다.
"""
from __future__ import annotations

import os
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RUN_DIR = os.path.join(ROOT, "runs", "eval")
HISTORY = os.path.join(ROOT, "docs", "eval_history.md")

HEADER = """# 평가 실행 이력

`eval/harness.py` 가 실행마다 한 줄을 덧붙인다. 지우지 않는다 — 이 표가
없으면 "무엇을 바꿨더니 올랐나" 를 말할 수 없다.

**읽는 법** — `근거없음오답` 을 먼저 본다. 문서에 근거가 없는 곳에서 값을
만든 건수다. 정확도가 올라도 이것이 늘면 개선이 아니다. 이 과제가 없애려는
문제를 재생산한 것이기 때문이다.

리포트 전문은 `runs/eval/<시각>-<stage>.md` 에 있다(Git 제외).

| 시각 | 단계 | 모델 | 문서 | 칸 | 정확도 | 근거없음오답 | 오답 | 미추출 | 정규화대기 | 페이지 | 비용(in/out) | 변경 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
"""


def _cost_str(parser) -> str:
    if parser is None or not getattr(parser, "calls", None):
        return "—"
    tin = sum(c["in"] for c in parser.calls)
    tout = sum(c["out"] for c in parser.calls)
    return f"{tin:,}/{tout:,}"


def _models(parser) -> str:
    if parser is None or not getattr(parser, "calls", None):
        return "—"
    return " · ".join(sorted({c["model"] for c in parser.calls}))


def archive(res, report: str, stage: str, note: str = "",
            parser=None, stamp: str | None = None) -> str:
    """리포트를 보관하고 이력에 한 줄 덧붙인다. → 보관 파일 경로.

    `stamp` 를 주면 그것을 쓴다(테스트에서 시각 의존을 없애기 위해).
    """
    stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
    os.makedirs(RUN_DIR, exist_ok=True)
    path = os.path.join(RUN_DIR, f"{stamp}-{stage}.md")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(report)

    n = res.counts()
    tot = sum(n.values())
    docs = [d for d, _f, st in res.docs if st == "채점"]
    good = n.get("정확", 0)
    acc = f"{good / tot * 100:.0f}%" if tot else "—"

    calls = [(d, g, p) for d, g, p in res.page_calls
             if g is not None and p is not None]
    page = (f"{sum(1 for _, g, p in calls if int(g) == int(p))}/{len(calls)}"
            if calls else "—")

    row = (f"| {stamp} | {stage} | {_models(parser)} | "
           f"{len(docs)}/{len(res.docs)} | {tot} | **{acc}** | "
           f"{n.get('근거없음오답', 0)} | {n.get('오답', 0)} | "
           f"{n.get('미추출', 0)} | {n.get('정규화대기', 0)} | {page} | "
           f"{_cost_str(parser)} | {note or '—'} |\n")

    os.makedirs(os.path.dirname(HISTORY), exist_ok=True)
    if not os.path.exists(HISTORY):
        with open(HISTORY, "w", encoding="utf-8", newline="\n") as f:
            f.write(HEADER)
    with open(HISTORY, "a", encoding="utf-8", newline="\n") as f:
        f.write(row)
    return path
