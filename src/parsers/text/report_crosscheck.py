# -*- coding: utf-8 -*-
"""조립된 파이프라인으로 골든셋을 돌려 **두 경로 대조**를 집계한다.

    python -m src.parsers.text.report_crosscheck            골든셋 전체
    python -m src.parsers.text.report_crosscheck d008 d010  일부만
    python -m src.parsers.text.report_crosscheck --no-vlm   텍스트 경로만 (API 호출 없음)

■ 무슨 작업인가
────────────────────────────────────────────────────────────────────
`DualParser` 가 필드마다 남기는 대조 결과는 `RawExtraction.note` 를 타고
`records.jsonl` · `events.jsonl` 까지 간다. 확인했다 —

    "두 경로가 다름 — VLM '12.051 / 12.249' vs 텍스트 '12.051' | 사람 확인 필요"

그런데 **집계가 어디에도 없다.** 문서 하나하나의 note 를 눈으로 세지 않으면
"두 경로가 몇 %에서 일치하는가" 를 말할 수 없다. 그 숫자가 이 장치의 존재 이유이고
(독립 두 경로가 같은 값을 내면 그 값은 믿을 만하다) 발표에서 쓸 수치다.

이 도구가 내는 것 셋:

    ① 정답 대조   골든셋 정답과 파이프라인 최종값이 같은가
    ② 합의율      두 경로가 다 읽은 칸 중 일치 비율
    ③ 문서별 표   어느 문서가 나쁜지 · Triage 가 버린 문서가 있는지

■ 비용
────────────────────────────────────────────────────────────────────
VLM 경로 문서는 **캐시 미적중 시 문서당 API 1회**를 쓴다(사양표 1장).
`build()` 가 응답 캐시를 켜므로 두 번째 실행부터는 호출이 없다.
`runs/vlm_cache` 를 지우면 다시 든다. 호출 없이 보려면 `--no-vlm`.

■ 왜 채점기(`score_against_kit`)와 따로 두는가
────────────────────────────────────────────────────────────────────
채점기는 **텍스트 파서만** 재고 정규화 이전 원문을 본다 — 파서의 책임 범위를
가리기 위한 것이다. 이 도구는 **파이프라인 최종값**(④ Normalize 이후)을 재고,
VLM 을 포함한 전 구간을 본다. 두 숫자는 다르며, 섞으면 어느 단계가 문제인지
알 수 없게 된다.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from eval.compare import same as _same                                 # noqa: E402
from src.parsers.text.crosscheck import (AGREE, CONFLICT, NOTATION,    # noqa: E402
                                         numeric_flag)
from src.parsers.text.field_index import FieldIndex                    # noqa: E402
from src.parsers.text.score_against_kit import find_file, read_kit     # noqa: E402
from src.parsers.text.sections import SectionIndex                     # noqa: E402

KIT = os.path.join(ROOT, "readme", "labeling_kit.xlsx")
RAW = os.path.join(ROOT, "raw_file")
OUT = os.path.join(ROOT, "runs", "crosscheck_report.md")

SKIP_VALUES = {"N/A", "NA", "판독불가", ""}


def _truth(row: dict) -> dict[str, str]:
    """킷 한 줄 → {field_key: 정답}. 채점 대상이 아닌 칸은 뺀다.

    엑셀이 숫자로 저장한 값(`300` → `300.0`)은 소수점을 뗀다 — 사람이 적은
    표기로 되돌리는 것이다(채점기와 같은 처리).
    """
    out = {}
    for key, (val, _name) in row["truth"].items():
        if val is None:
            continue
        text = (str(int(val)) if isinstance(val, float) and val.is_integer()
                else str(val).strip())
        text = text.lstrip("?").strip()
        if text.upper() in SKIP_VALUES:
            continue
        out[key] = text
    return out


def compare(truth: dict[str, str], records) -> tuple[int, int, list[tuple[str, str, str]]]:
    """정답 대조 — (맞음, 틀림, 틀린 목록). 값이 없는 칸은 세지 않는다.

    비교는 `eval/compare.same()` 에 맡긴다. 단위 표기·로마자 차이를 흡수하고
    `rules.yaml` 의 숫자 필드 표시까지 반영한다.
    """
    hit, miss, bad = 0, 0, []
    for r in records:
        want = truth.get(r.field_key)
        if not want or r.value is None:
            continue
        if _same(want, r.value, numeric_flag(r.field_key)):
            hit += 1
        else:
            miss += 1
            bad.append((r.field_key, want, str(r.value)))
    return hit, miss, bad


def agreement(agreements) -> tuple[int, int]:
    """(합의, 불일치). 한쪽만 읽은 칸은 분모에서 뺀다 — 대조가 불가능한 칸이다."""
    st = Counter(a.state for a in agreements)
    return st[AGREE] + st[NOTATION], st[CONFLICT]


def run(doc_ids: list[str] | None = None, use_vlm: bool = True,
        kit: str = KIT, root: str = RAW):
    """골든셋을 돌려 문서별 결과를 모은다."""
    from src.pipeline import build

    six = SectionIndex.load()
    ix = FieldIndex.load(section_names=six.name_map())
    rows, _cols, _unresolved = read_kit(kit, ix)
    p = build(use_vlm=use_vlm)

    out, tot = [], Counter()
    for row in rows:
        did = row["doc_id"]
        if doc_ids and did not in doc_ids:
            continue
        if row["cls"] == "out_of_scope":              # 킷이 제외한 문서
            continue
        path = find_file(root, row["file"])
        if path is None:
            out.append({"doc": did, "file": row["file"], "state": "파일 없음"})
            continue

        dual = p.vlm_parser
        if hasattr(dual, "last_agreements"):
            dual.last_agreements = []                # 직전 문서 결과가 남으면 안 된다
        doc = p.run_document(path)

        if doc.error:
            # Triage 가 범위 밖으로 판정한 문서도 여기 드러난다 — 조용히 빠지지 않는다
            out.append({"doc": did, "file": row["file"], "state": "처리 실패",
                        "why": doc.error[:120]})
            tot["실패"] += 1
            continue

        via_vlm = any(str(getattr(r, "parser", "")).endswith("VLM") for r in doc.records)
        hit, miss, bad = compare(_truth(row), doc.records)
        ok, no = agreement(getattr(dual, "last_agreements", []) if via_vlm else [])
        tot["맞음"] += hit; tot["틀림"] += miss
        tot["합의"] += ok; tot["불일치"] += no
        who = row.get("labeler", "미기재")
        tot[f"맞음:{who}"] += hit; tot[f"틀림:{who}"] += miss
        out.append({"doc": did, "file": row["file"], "state": "채점", "labeler": who,
                    "path": "VLM+텍스트" if via_vlm else "텍스트 단독",
                    "hit": hit, "miss": miss, "agree": ok, "conflict": no, "bad": bad})
    return out, tot


def render(results: list[dict], tot: Counter, use_vlm: bool) -> str:
    L = ["# 두 경로 대조 리포트", ""]
    n = tot["맞음"] + tot["틀림"]
    b = tot["합의"] + tot["불일치"]
    L.append(f"- 골든셋 {len(results)}건 · 정답 대조 **{tot['맞음']}/{n}**"
             + (f" ({tot['맞음'] / n:.0%})" if n else ""))
    if b:
        L.append(f"- 두 경로 합의 **{tot['합의']}/{b}** ({tot['합의'] / b:.0%}) "
                 f"· 불일치 {tot['불일치']}건 — 사람이 확인해야 하는 칸")
    else:
        L.append("- 두 경로 대조 없음 (VLM 경로를 타지 않았다)")
    if tot["실패"]:
        L.append(f"- ⚠️ 처리 실패 **{tot['실패']}건** — 아래 표에서 사유를 본다")
    # ── 라벨러별 (2026-08-26) ────────────────────────────────────
    #   골든셋에 AI 초안이 섞여 있다. 합쳐서 내면 "AI 가 만든 정답으로 AI 를 채점"
    #   한 부분이 숫자에 섞이므로 나눠 낸다. 발표에는 사람 검증분을 쓴다.
    whos = []
    for r in results:
        if r.get("labeler") and r["labeler"] not in whos:
            whos.append(r["labeler"])
    if len(whos) > 1:
        L += ["", "## 라벨러별", "", "| 라벨러 | 정답 대조 | |", "|---|---|---|"]
        for who in whos:
            h, m = tot[f"맞음:{who}"], tot[f"틀림:{who}"]
            L.append(f"| {who} | {h}/{h + m} | {h / (h + m) * 100:.0f}% |" if h + m
                     else f"| {who} | 0 | — |")
        human = [w for w in whos if "AI" not in w]
        if human and len(human) < len(whos):
            h = sum(tot[f"맞음:{w}"] for w in human)
            m = sum(tot[f"틀림:{w}"] for w in human)
            if h + m:
                L += ["", f"**사람이 검증한 정답만**: {h}/{h + m} ({h / (h + m) * 100:.0f}%)",
                      "", "> AI 초안은 사람 검증 전이다. 참고로만 본다."]

    L += ["", "## 문서별", "",
          "| 문서 | 파일 | 라벨러 | 경로 | 정답 대조 | 합의 | 불일치 |",
          "|---|---|---|---|---|---|---|"]
    for r in results:
        if r["state"] != "채점":
            L.append(f"| {r['doc']} | {r['file']} | | — | **{r['state']}** | | "
                     f"{r.get('why', '')} |")
            continue
        L.append(f"| {r['doc']} | {r['file']} | {r.get('labeler','')} | {r['path']} | "
                 f"{r['hit']}/{r['hit'] + r['miss']} | {r['agree']} | {r['conflict']} |")
    wrong = [(r["doc"], *b) for r in results if r["state"] == "채점" for b in r["bad"]]
    if wrong:
        # ── 틀린 칸을 필드별로 분해한다 (2026-08-26) ──────────────
        #   "73%" 만 보면 무엇을 고쳐야 할지 알 수 없다. 실제로 61칸을 갈라 보니
        #   절반 가까이가 **값은 같고 표기만 다른 것**이었다
        #   (`600#` vs `ANSI CLASS 600` · `FISHER` vs `Fisher Controls`).
        #   표준형을 정하면 사라지는 것과 판독을 고쳐야 하는 것은 다른 일이다.
        per = Counter(f for _d, f, _w, _g in wrong)
        first = {}
        for _d, f, w, g in wrong:
            first.setdefault(f, (w, g))
        L += ["", "## 틀린 칸 — 필드별", "",
              "| 필드 | 건수 | 예 (정답 → 파이프라인) |", "|---|---:|---|"]
        for f, n in per.most_common():
            w, g = first[f]
            L.append(f"| `{f}` | {n} | `{w}` → `{g}` |")

        L += ["", "## 정답과 다른 칸", "", "| 문서 | 필드 | 정답 | 파이프라인 |", "|---|---|---|---|"]
        for d, k, want, got in wrong:
            L.append(f"| {d} | {k} | `{want}` | `{got}` |")
    L += ["", "---", "",
          f"※ VLM {'포함' if use_vlm else '제외(--no-vlm)'}. 응답 캐시가 켜져 있어 "
          f"두 번째 실행부터는 API 호출이 없다 (`runs/vlm_cache`).", ""]
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="두 경로 대조 집계")
    ap.add_argument("docs", nargs="*", help="문서ID (없으면 전체)")
    ap.add_argument("--kit", default=KIT)
    ap.add_argument("--root", default=RAW)
    ap.add_argument("--no-vlm", action="store_true", help="텍스트 경로만 (API 호출 없음)")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args(argv)

    results, tot = run(a.docs or None, use_vlm=not a.no_vlm, kit=a.kit, root=a.root)
    text = render(results, tot, use_vlm=not a.no_vlm)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(text)

    n = tot["맞음"] + tot["틀림"]
    b = tot["합의"] + tot["불일치"]
    print(f"정답 대조 {tot['맞음']}/{n}" + (f" ({tot['맞음'] / n:.0%})" if n else "")
          + (f" · 합의 {tot['합의']}/{b} ({tot['합의'] / b:.0%})" if b else "")
          + (f" · 처리 실패 {tot['실패']}건" if tot["실패"] else ""))
    print(f"  {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
