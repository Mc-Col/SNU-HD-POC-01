# -*- coding: utf-8 -*-
"""평가 하네스 — 골든셋을 숫자로 바꾼다

    python -m eval.harness --no-vlm              텍스트 경로만 (API 불필요)
    python -m eval.harness --stage text          텍스트 파서만 채점
    python -m eval.harness --by fmt              포맷별로 분해
    python -m eval.harness --holdout d009,d010   이 문서는 채점에서 제외(잠금)

무엇을 재는가 — 실패를 국소화한다
─────────────────────────────────────────────────────────────
"정확도 82%" 는 개선에 쓸 수 없다. 어느 단계가 틀렸는지 모르면 무엇을 고칠지
알 수 없기 때문이다. 그래서 실패를 다섯 갈래로 나눈다.

    페이지오선택   골든셋의 사양표 페이지와 다른 페이지를 골랐다
    미추출        그 페이지에서 값을 찾지 못했다
    근거없음오답   정답이 N/A 인데 값을 만들었다  ← 가장 나쁜 실패
    정규화대기     맞는 칸을 집었으나 표준값 변환이 남았다
    오답          다른 값을 집었다

`정규화대기` 는 서경빈 선임의 `score_against_kit.py` 에서 가져온 구분이다.
파서는 문서 원문(`OPEN`)을 내고 표준값(`FAIL OPEN`)은 ④ Normalize 몫이므로,
"맞는 칸을 집었나" 와 "최종값이 맞나" 는 다른 측정이다.

`근거없음오답` 을 따로 세는 이유는 이 과제의 핵심 주장이 **모르면 만들지
않는다** 이기 때문이다. 이 숫자가 0 이 아니면 나머지 정확도는 의미가 없다.

집계 축
    equipment_class   컨트롤밸브 / 레귤레이터 — 레귤레이터는 필드가 구조적으로
                      없으므로 섞어 평균하면 정확도가 부풀고 안전 필드가 희석된다
    fmt · vintage     포맷·연식별 — 어느 경로가 약한지
    field             필드별 — 어느 필드가 약한지

홀드아웃
    개발 중 보지 않는 문서를 지정한다. 결과를 보고 규칙을 고치면 측정이
    무효가 되므로, 홀드아웃은 1회 개봉하고 그 사실을 리포트에 남긴다.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field as dc_field

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from eval import compare, groups                    # noqa: E402
from eval.kit import KitRow, locate, read_kit       # noqa: E402
from src import schema                              # noqa: E402

# 판정 — 나쁜 순서대로
VERDICTS = ("근거없음오답", "오답", "페이지오선택", "미추출", "정규화대기", "정확")
GOOD = ("정확",)
GRABBED = ("정확", "정규화대기")          # 맞는 칸을 집었다 (파서 책임 범위)


@dataclass
class Cell:
    doc_id: str
    field_key: str
    truth: str
    got: str | None
    verdict: str
    cls: str = groups.UNKNOWN
    fmt: str = ""
    vintage: str = ""
    uncertain: bool = False
    why: str = ""


@dataclass
class Result:
    cells: list[Cell] = dc_field(default_factory=list)
    docs: list[tuple[str, str, str]] = dc_field(default_factory=list)   # id, 파일, 상태
    page_calls: list[tuple[str, int | None, int | None]] = dc_field(default_factory=list)
    holdout: tuple[str, ...] = ()
    skipped_fields: list[str] = dc_field(default_factory=list)

    def counts(self, cells=None) -> dict[str, int]:
        c = Counter(x.verdict for x in (self.cells if cells is None else cells))
        return {v: c.get(v, 0) for v in VERDICTS}


# ── 판정 ────────────────────────────────────────────────────────

def judge(truth: str, got: str | None, raw: str | None, numeric: bool | None) -> tuple[str, str]:
    """한 칸의 판정. → (판정, 사유)"""
    if compare.is_na(truth):
        if got is None or compare.is_na(got):
            return "정확", ""
        return "근거없음오답", f"정답은 N/A 인데 {got!r} 을 만들었다"
    if got is None:
        return "미추출", f"정답 {truth!r} 을 찾지 못했다"
    if compare.same(truth, got, numeric):
        return "정확", ""
    # 표준값 변환만 남은 경우 — 파서는 원문을 낸다
    if raw is not None and compare.same(truth, raw, numeric):
        return "정확", ""
    if raw is not None and compare.norm_text(raw) and \
            compare.norm_text(raw) in compare.norm_text(truth):
        return "정규화대기", f"원문 {raw!r} 은 정답 {truth!r} 의 일부 — 표준값 변환 대기"
    if compare.norm_text(got) and compare.norm_text(got) in compare.norm_text(truth):
        return "정규화대기", f"{got!r} 은 정답 {truth!r} 의 일부 — 표준값 변환 대기"
    return "오답", compare.why(truth, got, numeric)


def _numeric(field_key: str) -> bool | None:
    """이 필드를 숫자로 대조할지. `rules.yaml` 이 알려주지 않으면 자동 판정."""
    for m in schema.value_aliases(field_key):
        if m.get("numeric") is not None:
            return bool(m["numeric"])
    return None


# ── 채점 ────────────────────────────────────────────────────────

def score(rows: list[KitRow], extract, holdout: tuple[str, ...] = (),
          only_mvp: bool = False) -> Result:
    """골든셋을 채점한다.

    extract(row) → (values, raws, spec_page)
        values     {field_key: 최종값 | None}
        raws       {field_key: 문서 원문 | None}   (없으면 {})
        spec_page  고른 사양표 페이지 (없으면 None)
      파이프라인이든 파서 하나든, 이 모양만 맞추면 채점된다.
    """
    res = Result(holdout=tuple(holdout))
    keys = {f.key for f in (schema.mvp_fields() if only_mvp else schema.all_fields())}

    for row in rows:
        if row.doc_id in holdout:
            res.docs.append((row.doc_id, row.file, "홀드아웃 — 채점 제외"))
            continue
        if not row.path:
            res.docs.append((row.doc_id, row.file, "파일 없음"))
            continue
        try:
            values, raws, spec_page = extract(row)
        except Exception as e:
            res.docs.append((row.doc_id, row.file, f"처리 실패: {type(e).__name__} {e}"))
            continue

        cls = groups.equipment_class(row.truth)
        page_ok = (spec_page is None or row.spec_page is None
                   or int(spec_page) == int(row.spec_page))
        res.page_calls.append((row.doc_id, row.spec_page, spec_page))
        res.docs.append((row.doc_id, row.file,
                         "채점" if page_ok else f"페이지 오선택 (정답 p{row.spec_page} / 선택 p{spec_page})"))

        absent = groups.expected_absent(cls)
        for key, truth in row.truth.items():
            if key not in keys:
                continue
            if key in absent and compare.is_na(truth):
                continue        # 이 설비에 구조적으로 없는 필드 — 쉬운 정답을 세지 않는다
            unc = str(truth).strip().startswith("?")
            if not page_ok:
                v, w = "페이지오선택", f"정답 p{row.spec_page} 대신 p{spec_page} 를 읽었다"
            else:
                v, w = judge(truth, values.get(key), (raws or {}).get(key), _numeric(key))
            res.cells.append(Cell(row.doc_id, key, truth, values.get(key), v,
                                  cls, row.fmt, row.vintage, unc, w))
    return res


# ── 리포트 ──────────────────────────────────────────────────────

def _rate(cells, wanted) -> str:
    if not cells:
        return "—"
    hit = sum(1 for c in cells if c.verdict in wanted)
    return f"{hit / len(cells) * 100:.0f}%"


def render(res: Result, by: str = "") -> str:
    n = res.counts()
    tot = sum(n.values())
    L = ["# 평가 하네스 — 골든셋 대조", ""]
    if res.holdout:
        L += [f"> 홀드아웃 {', '.join(res.holdout)} — 채점에서 제외했다. "
              f"개봉하면 그 사실을 여기 남긴다.", ""]
    if not tot:
        return "\n".join(L + ["채점된 칸이 없다. `--stage` 와 파일 경로를 확인할 것.", ""])

    scored = [d for d, _f, st in res.docs if st == "채점"]
    unscored = [(d, st) for d, _f, st in res.docs if st != "채점"]

    L += [f"- 문서 **{len(scored)}/{len(res.docs)}건 채점** · 칸 **{tot}개**",
          f"- **최종 정확도 {_rate(res.cells, GOOD)}** — 표준값까지 맞은 비율",
          f"- 칸 적중률 {_rate(res.cells, GRABBED)} — 맞는 칸을 집었는가 (파서 책임 범위)",
          ""]

    # 조용한 미측정을 헤드라인에 올린다 — 안 잰 것이 성공처럼 보이면 안 된다
    if unscored:
        L += [f"> ⚠ **{len(unscored)}건이 채점되지 않았다.** 이 정확도는 "
              f"{len(scored)}건에 대한 것이며 전체를 대표하지 않는다.", ""]
        why = defaultdict(list)
        for d, st in unscored:
            why[st.split(":")[0].split("(")[0].strip()].append(d)
        L += ["| 미채점 사유 | 문서 |", "|---|---|"]
        L += [f"| {k} | {', '.join(v)} |" for k, v in sorted(why.items())]
        L += [""]

    bad = n["근거없음오답"]
    if bad:
        L += [f"> ⚠ **근거없음오답 {bad}건** — 정답이 N/A 인데 값을 만들었다. "
              f"이 숫자가 0 이 아니면 나머지 정확도는 의미가 없다.", ""]
    else:
        L += ["> **근거없음오답 0건** — 문서에 근거가 없는 곳에서 값을 만들지 않았다.", ""]

    L += ["| 판정 | 건수 | 비율 |", "|---|---|---|"]
    for v in VERDICTS:
        L.append(f"| {v} | {n[v]} | {n[v] / tot * 100:.0f}% |")
    L += [""]

    # 설비 분류별 — 섞어 평균하면 안 되는 축
    L += ["## 설비 분류별", "",
          "레귤레이터는 필드가 구조적으로 없어 따로 낸다. 섞으면 정확도가 부풀고 안전 필드가 희석된다.",
          "", "| 분류 | 칸 | 최종 정확도 | 칸 적중률 |", "|---|---|---|---|"]
    for cls in (groups.CONTROL_VALVE, groups.REGULATOR, groups.UNKNOWN):
        cc = [c for c in res.cells if c.cls == cls]
        if cc:
            L.append(f"| {cls} | {len(cc)} | {_rate(cc, GOOD)} | {_rate(cc, GRABBED)} |")
    L += [""]

    # 안전·식별 필드 — 컨트롤밸브만
    safe_keys = {f.key for f in schema.safety_fields()}
    sc = [c for c in res.cells
          if c.field_key in safe_keys and groups.scoreable(c.cls, c.field_key)]
    if sc:
        L += ["## 안전·식별 필드", "",
              f"컨트롤밸브만으로 낸다 — 레귤레이터에는 `actuator_fail_action` 이 없다.", "",
              f"- 칸 {len(sc)}개 · **정확도 {_rate(sc, GOOD)}**",
              f"- 오적재 위험(오답 + 근거없음오답) "
              f"{sum(1 for c in sc if c.verdict in ('오답', '근거없음오답'))}건", ""]

    # 페이지 선택
    calls = [(d, g, p) for d, g, p in res.page_calls if g is not None and p is not None]
    if calls:
        ok = sum(1 for _, g, p in calls if int(g) == int(p))
        L += ["## 사양표 페이지 선택", "",
              f"- {ok}/{len(calls)} 정답 ({ok / len(calls) * 100:.0f}%)", ""]
        wrong = [(d, g, p) for d, g, p in calls if int(g) != int(p)]
        if wrong:
            L += ["| 문서 | 정답 | 선택 |", "|---|---|---|"]
            L += [f"| {d} | p{g} | p{p} |" for d, g, p in wrong] + [""]

    # 분해 축
    if by:
        L += [f"## {by} 별", "", f"| {by} | 칸 | 최종 정확도 | 칸 적중률 |", "|---|---|---|---|"]
        bucket = defaultdict(list)
        for c in res.cells:
            bucket[getattr(c, by, "")].append(c)
        for k in sorted(bucket):
            cc = bucket[k]
            L.append(f"| {k or '—'} | {len(cc)} | {_rate(cc, GOOD)} | {_rate(cc, GRABBED)} |")
        L += [""]

    # 약한 필드
    per = defaultdict(list)
    for c in res.cells:
        per[c.field_key].append(c)
    weak = sorted(((k, cc) for k, cc in per.items()
                   if any(x.verdict not in GOOD for x in cc)),
                  key=lambda kv: sum(1 for x in kv[1] if x.verdict in GOOD) / len(kv[1]))
    if weak:
        L += ["## 약한 필드", "", "| 필드 | 칸 | 정확도 | 주요 실패 |", "|---|---|---|---|"]
        for k, cc in weak[:12]:
            f = Counter(x.verdict for x in cc if x.verdict not in GOOD).most_common(1)
            L.append(f"| `{k}` | {len(cc)} | {_rate(cc, GOOD)} | {f[0][0] if f else '—'} |")
        L += [""]

    # 문서별
    L += ["## 문서별", "", "| 문서 | 파일 | 상태 |", "|---|---|---|"]
    L += [f"| {d} | {f} | {st} |" for d, f, st in res.docs] + [""]

    # 불확실 라벨
    unc = [c for c in res.cells if c.uncertain]
    if unc:
        L += [f"> 라벨러가 확신하지 못한 칸 {len(unc)}개(`?` 표시) 포함. "
              f"이 칸의 정확도는 참고치다.", ""]

    # 실패 상세
    fails = [c for c in res.cells if c.verdict not in GOOD]
    if fails:
        L += ["## 실패 상세", "", "| 문서 | 필드 | 정답 | 추출 | 판정 | 사유 |",
              "|---|---|---|---|---|---|"]
        for c in sorted(fails, key=lambda x: (VERDICTS.index(x.verdict), x.doc_id)):
            L.append(f"| {c.doc_id} | `{c.field_key}` | {c.truth} | "
                     f"{c.got if c.got is not None else '—'} | {c.verdict} | {c.why} |")
        L += [""]

    L += ["---", "",
          "**100% 정확도는 증명할 수 없다.** 100건에서 오류 0건이면 95% 신뢰상한이 "
          "약 3% 다(rule of three). 표본이 11건이면 상한은 훨씬 넓다.", ""]
    return "\n".join(L)


# ── 추출기 — 지금 존재하는 것으로 채점한다 ──────────────────────

def make_text_extractor():
    """텍스트 파서만으로 추출한다(서경빈 선임 모듈). API 불필요.

    스캔 페이지는 텍스트가 없으므로 값이 비고, 그 사실이 `미추출` 로 잡힌다 —
    그것이 정직한 결과다. `--no-vlm` 이 재는 것이 바로 이 경계다.
    """
    from src.parsers.text.excel import parse_excel
    from src.parsers.text.pdf_text import parse_pdf_text

    def extract(row: KitRow):
        ext = os.path.splitext(row.path)[1].lower()
        page = row.spec_page
        if ext in (".xlsx", ".xlsm", ".xls"):
            res = parse_excel(row.path, sheets=[int(page)] if page else None)
        elif ext == ".pdf":
            res = parse_pdf_text(row.path, pages=[int(page)] if page else None)
        else:
            raise RuntimeError(f"{ext} 는 텍스트 경로가 없다 — VLM 담당")
        raws = {r.field_key: r.raw_value for r in res.records if r.found}
        return dict(raws), raws, page      # 파서는 원문만 낸다 (Normalize 이전)
    return extract


def make_vlm_extractor(only_mvp: bool = False):
    """VLM 으로 추출하고 ④ Normalize 까지 적용한다.

    파서는 문서 원문(`Close`)을 내고 표준값(`FAIL CLOSE`)은 Normalize 몫이다.
    둘을 함께 돌려주므로 하네스가 `정규화대기` 와 `오답` 을 구분할 수 있다.

    사양표 페이지는 **골든셋의 값을 쓴다.** 학습이 아니라 변수 통제다 —
    "VLM 이 값을 읽나" 와 "맞는 페이지를 찾나" 를 분리해서 재기 위한 것이고,
    페이지 선택은 Triage 가 붙은 뒤 따로 측정한다.
    """
    from src.contracts import DocumentClass, PageClass, PageInfo, TriageResult
    from src.parsers.vlm.openai_vlm import VlmParser
    from src.pipeline import DefaultNormalize

    parser = VlmParser()
    norm = DefaultNormalize()
    fields = schema.mvp_fields() if only_mvp else schema.all_fields()
    fields = [f for f in fields if f.source == "document"]   # 파생 필드는 VLM 몫이 아니다

    def extract(row: KitRow):
        page = int(row.spec_page or 1)
        tri = TriageResult(
            source_path=row.path, document_class=DocumentClass.DATASHEET,
            pages=[PageInfo(page=page, page_class=PageClass.SPEC, selected=True)])
        recs = parser.extract(row.path, tri, fields)
        raws = {r.field_key: r.raw_value for r in recs if r.found}
        values = {}
        for r in recs:
            f = schema.get(r.field_key)
            v, _trace = norm.run(r, f)
            values[r.field_key] = v
        return values, raws, page

    extract.parser = parser        # 비용 요약을 리포트에 쓴다
    return extract


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="골든셋 평가 하네스")
    ap.add_argument("--kit", default="readme/labeling_kit.xlsx")
    ap.add_argument("--root", default="raw_file")
    ap.add_argument("--stage", default="text", choices=["text", "vlm", "pipeline"],
                    help="text = 텍스트 파서만 / vlm = VLM + Normalize / "
                         "pipeline = 전체 (모듈 구현 후)")
    ap.add_argument("--no-vlm", action="store_true", help="VLM 경로를 쓰지 않는다")
    ap.add_argument("--only-mvp", action="store_true", help="MVP 9필드만")
    ap.add_argument("--by", default="fmt", choices=["", "fmt", "vintage", "cls"])
    ap.add_argument("--holdout", default="", help="쉼표로 구분한 문서ID")
    ap.add_argument("--out", default="runs/eval_report.md")
    a = ap.parse_args(argv)

    rows = read_kit(a.kit)
    missing = locate(rows, a.root)
    if read_kit.unmatched:
        print(f"⚠ 스키마에 없는 킷 컬럼: {', '.join(read_kit.unmatched)}", file=sys.stderr)
    if missing:
        print(f"⚠ 파일을 찾지 못함: {', '.join(missing)}", file=sys.stderr)

    if a.stage == "pipeline":
        print("전체 파이프라인 채점은 Triage·Router 구현 후 붙인다. "
              "지금은 --stage text 또는 --stage vlm 을 쓸 것.", file=sys.stderr)
        return 2
    if a.stage == "vlm" and a.no_vlm:
        print("--stage vlm 과 --no-vlm 은 함께 쓸 수 없다.", file=sys.stderr)
        return 2

    if a.stage == "vlm":
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT, ".env"))
        extract = make_vlm_extractor(only_mvp=a.only_mvp)
    else:
        extract = make_text_extractor()
    holdout = tuple(x.strip() for x in a.holdout.split(",") if x.strip())
    res = score(rows, extract, holdout, only_mvp=a.only_mvp)
    report = render(res, a.by)
    parser = getattr(extract, "parser", None)
    if parser is not None and parser.calls:
        cost = parser.cost_summary()
        rows_md = ["", "## 비용", "",
                   "| 모델 | 호출 | 입력 토큰 | 출력 토큰 |", "|---|---|---|---|"]
        for m, c in sorted(cost.items()):
            rows_md.append(f"| `{m}` | {c['calls']} | {c['in']:,} | {c['out']:,} |")
        rows_md += ["", "이미지 토큰이 입력의 대부분이다. 비용을 줄이려면 "
                    "페이지 수를 먼저 줄인다 — 격자 1장으로 판정하는 이유다.", ""]
        report += "\n".join(rows_md)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(report)
    print(report)
    print(f"\n저장: {a.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
