# -*- coding: utf-8 -*-
"""미매핑 라벨 리포트 — 유사표현 사전을 실물에서 수집한다.

문서를 훑어 표준 컬럼에 붙지 못한 라벨을 모으고, 빈도와 추천 필드를 붙여 낸다.
30필드의 표기 변종을 사람이 상상해서 채우는 것은 불가능하므로 실물에서 나와야 한다.

  python -m src.parsers.text.report_unmapped raw_file --out runs/unmapped.md
  python -m src.parsers.text.report_unmapped raw_file --limit 200 --with-values

기본값은 값을 출력하지 않는다 — 라벨링 중인 문서의 정답을 미리 보면 앵커링된다.
"""
from __future__ import annotations

import argparse
import collections
import difflib
import fnmatch
import os
import sys
from dataclasses import dataclass, field as dc_field

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.parsers.text.composite import CompositeIndex          # noqa: E402
from src.parsers.text.excel import parse_excel                 # noqa: E402
from src.parsers.text.field_index import FieldIndex, normalize_label  # noqa: E402
from src.parsers.text.sections import SectionIndex                 # noqa: E402
from src.parsers.text.pdf_text import parse_pdf_text           # noqa: E402

EXCEL_EXT = {".xlsx", ".xlsm"}
PDF_EXT = {".pdf"}
SKIP_EXT = {".tif", ".tiff", ".xls", ".doc", ".docx", ".png", ".jpg"}

MIN_PDF_CHARS = 200          # 이보다 적으면 스캔 PDF 로 보고 VLM 에 넘긴다
SIMILARITY = 0.62            # 추천 필드로 인정할 최소 유사도
MIN_ALPHA = 3                # 알파벳이 이보다 적으면 라벨로 보지 않는다 (값·기호 걸러내기)
MIN_BOOST_LEN = 4            # 부분일치 가산은 양쪽이 이 길이 이상일 때만
MAX_WORDS = 6                # 이보다 길면 항목명이 아니라 문장·비고로 본다
MIN_PART_ALPHA = 3           # 복합 라벨 조각이 가져야 할 최소 알파벳 수 (단위 걸러내기)


@dataclass
class LabelStat:
    text: str
    count: int = 0
    files: set[str] = dc_field(default_factory=set)
    example_value: str = ""

    @property
    def file_count(self) -> int:
        return len(self.files)


@dataclass
class Report:
    labels: dict[str, LabelStat] = dc_field(default_factory=dict)
    coverage: list[tuple[str, int]] = dc_field(default_factory=list)
    skipped: list[tuple[str, str]] = dc_field(default_factory=list)
    field_hits: collections.Counter = dc_field(default_factory=collections.Counter)


def _is_text_pdf(path: str) -> bool:
    import fitz
    try:
        with fitz.open(path) as d:
            n = min(d.page_count, 3)
            return sum(len(d[i].get_text().strip()) for i in range(n)) >= MIN_PDF_CHARS
    except Exception:
        return False


def collect(paths: list[str], ix: FieldIndex, cix: CompositeIndex,
            keep_values: bool) -> Report:
    rep = Report()
    for p in paths:
        ext = os.path.splitext(p)[1].lower()
        try:
            if ext in EXCEL_EXT:
                res = parse_excel(p, index=ix, composite=cix)
            elif ext in PDF_EXT:
                if not _is_text_pdf(p):
                    rep.skipped.append((p, "스캔 PDF — VLM 담당"))
                    continue
                res = parse_pdf_text(p, index=ix, composite=cix)
            elif ext in SKIP_EXT:
                rep.skipped.append((p, f"{ext} 미지원 — VLM 또는 범위 외"))
                continue
            else:
                rep.skipped.append((p, f"{ext} 알 수 없는 확장자"))
                continue
        except Exception as e:                       # 실패를 삼키지 않는다
            rep.skipped.append((p, f"파싱 실패: {type(e).__name__} {e}"))
            continue

        found = [r for r in res.records if r.found]
        rep.coverage.append((p, len(found)))
        for r in found:
            rep.field_hits[r.field_key] += 1

        for u in res.unmapped:
            text = " ".join(u.text.split())
            if not text or len(text) > 40:
                continue
            if sum(c.isalpha() and c.isascii() for c in text) < MIN_ALPHA:
                continue                     # 값·단위·기호는 라벨이 아니다
            if len(text.split()) > MAX_WORDS:
                continue                     # 문장·비고는 항목명이 아니다
            if len(normalize_label(text)) < MIN_ALPHA:
                continue
            st = rep.labels.setdefault(normalize_label(text), LabelStat(text))
            st.count += 1
            st.files.add(os.path.basename(p))
            if keep_values and not st.example_value:
                st.example_value = " ".join(u.neighbor_value.split())[:40]
    return rep


def suggest(label: str, ix: FieldIndex, names: list[tuple[str, str]]) -> tuple[str, float]:
    """가장 가까운 표준 필드. (필드명, 유사도)"""
    key = normalize_label(label)
    best, score = "", 0.0
    if len(key) < MIN_ALPHA:
        return "", 0.0
    for norm, name in names:
        r = difflib.SequenceMatcher(None, key, norm).ratio()
        # 부분일치 가산 — 짧은 조각이 긴 필드명에 우연히 들어가는 것을 막는다
        if (len(norm) >= MIN_BOOST_LEN and len(key) >= MIN_BOOST_LEN
                and (norm in key or key in norm)):
            cover = min(len(norm), len(key)) / max(len(norm), len(key))
            r = max(r, 0.60 + 0.35 * cover)
        if r > score:
            best, score = name, r
    return (best, round(score, 2)) if score >= SIMILARITY else ("", round(score, 2))


def render(rep: Report, ix: FieldIndex, cix: CompositeIndex, top: int) -> tuple[str, str]:
    names = [(normalize_label(f), f) for f in _field_names(ix)]
    rows = sorted(rep.labels.values(), key=lambda s: (-s.count, s.text))

    known_composite = {normalize_label(r.label) for r in cix.rules}
    names0 = [(normalize_label(f), f) for f in _field_names(ix)]
    comp, plain = [], []
    for s in rows:
        if ("/" in s.text and normalize_label(s.text) not in known_composite
                and _looks_composite(s.text, ix, names0)):
            comp.append(s)
        else:
            plain.append(s)

    L = ["# 미매핑 라벨 리포트", ""]
    n_ok = len(rep.coverage)
    avg = sum(c for _, c in rep.coverage) / n_ok if n_ok else 0
    L += [f"- 처리 {n_ok}건 · 건너뜀 {len(rep.skipped)}건",
          f"- 평균 매핑 **{avg:.1f} / {ix.field_count}** 필드",
          f"- 미매핑 라벨 **{len(rep.labels)}종**", ""]

    scored = [(st, *suggest(st.text, ix, names)) for st in plain]
    hit = sorted([x for x in scored if x[1]], key=lambda x: (-x[2], -x[0].count))
    miss = [x for x in scored if not x[1]][:top]
    show_val = any(st.example_value for st in rows)
    head = "| 문서 표기 | 건수 | 파일수 | 추천 표준 필드 | 유사도 |" + (" 값 예시 |" if show_val else "")
    bar = "|---|---:|---:|---|---:|" + ("---|" if show_val else "")

    L += [f"## ① 추천 필드가 있는 라벨 — {len(hit)}종 (유사도순)", "",
          "여기가 사전에 넣을 후보다. 유사도는 참고값일 뿐이니 표기를 직접 보고 판단할 것.",
          "", head, bar]
    for st, f, sc in hit[:top]:
        val = f" {st.example_value} |" if show_val else ""
        L.append(f"| `{st.text}` | {st.count} | {st.file_count} | {f} | {sc} |{val}")

    L += ["", f"## ② 추천 없는 라벨 — {len([x for x in scored if not x[1]])}종 (빈도순)", "",
          "양식 머리글(Customer·Project 등)이거나 마스터 스키마에 대응 필드가 없는 항목이 대부분.",
          "", head, bar]
    for st, f, sc in miss:
        val = f" {st.example_value} |" if show_val else ""
        L.append(f"| `{st.text}` | {st.count} | {st.file_count} | — | {sc} |{val}")

    if comp:
        L += ["", f"## ③ 복합 라벨 후보 — {len(comp)}종 (rules.yaml 검토용)", "",
              "조각 중 하나 이상이 표준 필드로 보이는 것만 실었다. 단위(kg/cm2)는 제외.", "",
              "| 문서 표기 | 건수 | 조각 |", "|---|---:|---|"]
        for s in comp[:top // 2]:
            parts = " · ".join(p.strip() for p in s.text.split("/") if p.strip())
            L.append(f"| `{s.text}` | {s.count} | {parts} |")

    if rep.field_hits:
        L += ["", "## 이미 매핑되는 필드", "",
              ", ".join(f"{k}({v})" for k, v in rep.field_hits.most_common())]

    if rep.skipped:
        why = collections.Counter(r for _, r in rep.skipped)
        L += ["", "## 건너뛴 파일", ""]
        L += [f"- {r} — {n}건" for r, n in why.most_common()]

    # 엑셀에 붙여 넣을 TSV
    T = ["문서표기\t건수\t파일수\t추천필드\t유사도\t채택(O/X)"]
    for st, f, sc in hit[:top] + miss:
        T.append(f"{st.text}\t{st.count}\t{st.file_count}\t{f}\t{sc}\t")
    return "\n".join(L) + "\n", "\n".join(T) + "\n"


def _looks_composite(text: str, ix: FieldIndex, names: list[tuple[str, str]]) -> bool:
    """구분자가 있다고 다 복합 라벨은 아니다. 단위(kg/cm2)와 값을 걸러낸다.

    조각이 둘 이상이고, 각 조각이 알파벳을 충분히 가지며,
    적어도 한 조각이 표준 필드로 추천되어야 후보로 본다.
    """
    parts = [p.strip() for p in text.split("/") if p.strip()]
    if len(parts) < 2:
        return False
    if any(sum(c.isalpha() and c.isascii() for c in p) < MIN_PART_ALPHA for p in parts):
        return False
    return any(suggest(p, ix, names)[0] for p in parts)


def _field_names(ix: FieldIndex) -> list[str]:
    import yaml
    from src.parsers.text.field_index import SCHEMA_PATH
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        return [x["name"] for x in yaml.safe_load(f)["fields"]]


def gather(root: str, excludes: list[str], limit: int | None) -> list[str]:
    out = []
    for dirpath, _, files in os.walk(root):
        for name in sorted(files):
            p = os.path.join(dirpath, name)
            rel = os.path.relpath(p, ROOT)
            if any(fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(name, pat)
                   for pat in excludes):
                continue
            out.append(p)
    out.sort()
    return out[:limit] if limit else out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="미매핑 라벨 리포트")
    ap.add_argument("root", help="훑을 폴더 또는 파일")
    ap.add_argument("--out", default="runs/unmapped_report.md")
    ap.add_argument("--limit", type=int, default=None, help="앞에서 N개만")
    ap.add_argument("--top", type=int, default=120, help="리포트에 실을 라벨 수")
    ap.add_argument("--exclude", action="append", default=[],
                    help="제외할 glob (여러 번 지정 가능)")
    ap.add_argument("--with-values", action="store_true",
                    help="값 예시를 함께 싣는다 (라벨링 중인 문서에는 쓰지 말 것 — 앵커링)")
    a = ap.parse_args(argv)

    paths = [a.root] if os.path.isfile(a.root) else gather(a.root, a.exclude, a.limit)
    print(f"대상 {len(paths)}건 훑는 중...", flush=True)

    six = SectionIndex.load()
    ix, cix = FieldIndex.load(section_names=six.name_map()), CompositeIndex.load()
    rep = collect(paths, ix, cix, a.with_values)
    md, tsv = render(rep, ix, cix, a.top)

    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(md)
    tsv_path = os.path.splitext(a.out)[0] + ".tsv"
    with open(tsv_path, "w", encoding="utf-8") as f:
        f.write(tsv)

    n_ok = len(rep.coverage)
    avg = sum(c for _, c in rep.coverage) / n_ok if n_ok else 0
    print(f"처리 {n_ok}건 · 건너뜀 {len(rep.skipped)}건 · 평균 매핑 {avg:.1f}/{ix.field_count}")
    print(f"미매핑 라벨 {len(rep.labels)}종")
    print(f"  리포트 {a.out}\n  붙여넣기용 {tsv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
