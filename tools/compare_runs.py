# -*- coding: utf-8 -*-
"""두 실행을 칸 단위로 맞대 본다.

    python tools/compare_runs.py runs/raw/base28b runs/raw/terra28

왜 헤드라인으로는 안 되나
─────────────────────────────────────────────────────────────
같은 문서를 같은 지시문으로 다시 돌려도 **578칸 중 약 19칸(3%)이 흔들린다**
(2026-08-25 실측). 그래서 정확도가 1~2%p 움직인 것은 아무 뜻이 없다.

개선을 주장하려면 **변화가 뭉쳐 있어야** 한다 — 어느 필드, 어느 판정에서.
포지셔너 추론을 껐을 때 14칸이 한 필드에 몰렸고, 그래서 지목이 되었다.
흩어져 있으면 잡음이다.

무엇을 내는가
    ① 판정 전이 행렬     무엇이 무엇으로 바뀌었나
    ② 필드별 순증감       뭉쳐 있나 흩어져 있나
    ③ 비용
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from eval import harness                                     # noqa: E402
from eval.kit import locate, read_kit                        # noqa: E402
from eval.store import RawStore                              # noqa: E402

GOOD = harness.GOOD
ORDER = harness.VERDICTS          # 나쁜 것부터


def _score(path, rows, only_mvp):
    ex = harness.make_replay_extractor(path, only_mvp=only_mvp)
    res = harness.score(rows, ex, only_mvp=only_mvp)
    return {(c.doc_id, c.field_key): c for c in res.cells}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("before")
    ap.add_argument("after")
    ap.add_argument("--kit", default="readme/labeling_kit.xlsx")
    ap.add_argument("--root", default="raw_file")
    ap.add_argument("--only-mvp", action="store_true")
    ap.add_argument("--split", default="d011",
                    help="이 문서ID 이하를 '초기'로 본다. 과적합 판별용")
    a = ap.parse_args(argv)

    rows = read_kit(a.kit)
    locate(rows, a.root)

    A = _score(a.before, rows, a.only_mvp)
    B = _score(a.after, rows, a.only_mvp)
    keys = sorted(set(A) & set(B))

    ma, mb = RawStore(a.before).meta(), RawStore(a.after).meta()
    print(f"이전 {a.before}  {' · '.join(ma.get('models') or ['?'])}")
    print(f"이후 {a.after}  {' · '.join(mb.get('models') or ['?'])}")
    print(f"공통 채점 칸 {len(keys)}")

    accA = sum(1 for k in keys if A[k].verdict in GOOD)
    accB = sum(1 for k in keys if B[k].verdict in GOOD)
    print(f"\n정확 {accA} → {accB}  ({accA / len(keys) * 100:.0f}% → "
          f"{accB / len(keys) * 100:.0f}%)  순증감 {accB - accA:+d}칸")

    # ── ① 판정 전이 ────────────────────────────────────────
    trans = Counter((A[k].verdict, B[k].verdict) for k in keys
                    if A[k].verdict != B[k].verdict)
    print(f"\n── 판정이 바뀐 칸 {sum(trans.values())} " + "─" * 34)
    for (x, y), n in sorted(trans.items(), key=lambda t: -t[1]):
        arrow = "개선" if ORDER.index(y) > ORDER.index(x) else "악화"
        print(f"   {n:3d}  {x:12s} → {y:12s}  {arrow}")

    # ── ② 필드별 뭉침 ──────────────────────────────────────
    per = defaultdict(lambda: [0, 0])
    for k in keys:
        if A[k].verdict == B[k].verdict:
            continue
        better = ORDER.index(B[k].verdict) > ORDER.index(A[k].verdict)
        per[k[1]][0 if better else 1] += 1
    print(f"\n── 필드별 (변화가 뭉쳐 있나) " + "─" * 34)
    print(f"   {'필드':30s} {'개선':>4s} {'악화':>4s} {'순':>4s}")
    rank = sorted(per.items(), key=lambda t: -(t[1][0] - t[1][1]))
    for f, (g, b) in rank:
        if g or b:
            print(f"   {f:30s} {g:4d} {b:4d} {g - b:+4d}")

    net = sorted((g - b for g, b in per.values()), key=abs, reverse=True)
    top = sum(abs(x) for x in net[:3])
    tot = sum(abs(x) for x in net) or 1
    print(f"\n   상위 3개 필드가 순변화의 {top / tot * 100:.0f}% 를 차지한다 — "
          + ("**뭉쳐 있다. 지목 가능**" if top / tot > 0.5
             else "흩어져 있다. 잡음일 가능성이 크다"))

    # ── ③ 과적합 판별 — 규칙을 만든 문서 vs 나중에 들어온 문서 ──
    #
    # 규칙을 늘리면 언제나 초기 문서의 정확도가 오른다(그 규칙이 거기서 나왔다).
    # **일반화되는지는 두 집단의 격차가 같은지로 갈린다.**
    #   격차가 양쪽에서 같다   → 진짜 지식이다
    #   초기에서만 크다        → 과적합이다
    g = {}
    for era in ("초기", "이후"):
        ks = [k for k in keys
              if (k[0] <= a.split) == (era == "초기")]
        if not ks:
            continue
        oa = sum(1 for k in ks if A[k].verdict in GOOD)
        ob = sum(1 for k in ks if B[k].verdict in GOOD)
        g[era] = (len(ks), oa, ob)
    if len(g) == 2:
        print("")
        print(f"── 과적합 판별 (기준 {a.split} 이하 = 초기) " + "─" * 20)
        print(f"   {'집단':6s} {'칸':>5s} {'이전':>7s} {'이후':>7s} {'격차':>7s}")
        gaps = {}
        for era, (n, oa, ob) in g.items():
            pa, pb = oa / n * 100, ob / n * 100
            gaps[era] = pb - pa
            print(f"   {era:6s} {n:5d} {pa:6.1f}% {pb:6.1f}% {pb - pa:+6.1f}%p")
        d = gaps["이후"] - gaps["초기"]
        print()
        if abs(d) < 2:
            print("   두 집단의 격차가 비슷하다 — **일반화되는 지식**으로 보인다.")
        elif gaps["초기"] > gaps["이후"]:
            print(f"   초기에서만 이득이 크다({d:+.1f}%p 차) — "
                  "**과적합 신호**. 규칙이 만든 문서에만 듣는다.")
        else:
            print(f"   이후에서 이득이 더 크다({d:+.1f}%p 차) — "
                  "과적합이 아니라 오히려 일반화된다.")

    # ── ④ 비용 ─────────────────────────────────────────────
    print(f"\n── 비용 " + "─" * 52)
    for tag, m in (("이전", ma), ("이후", mb)):
        ti, to = m.get("tokens_in"), m.get("tokens_out")
        if ti:
            print(f"   {tag}  입력 {ti:,} · 출력 {to:,}")
    if ma.get("tokens_in") and mb.get("tokens_in"):
        r_in = mb["tokens_in"] / ma["tokens_in"]
        r_out = mb["tokens_out"] / ma["tokens_out"]
        print(f"   비율  입력 {r_in:.2f}배 · 출력 {r_out:.2f}배 "
              f"(같은 문서·같은 지시문이므로 모델 차이다)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
