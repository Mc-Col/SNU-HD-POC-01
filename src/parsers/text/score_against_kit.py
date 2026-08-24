# -*- coding: utf-8 -*-
"""골든셋(라벨링 킷) 대비 텍스트 파서 채점 — 내 모듈 자기 검증.

  python -m src.parsers.text.score_against_kit \
      --kit ~/snu_ai/poc_team/labeling_kit.xlsx --root "raw_file/서경빈 선임님"

이 도구는 src/parsers/text 만 잰다. 파이프라인 전체 평가 하네스(eval/)와 다르다.
정답이 아직 AI 초안이면 숫자는 잠정치다 — 사람 검수 뒤 다시 돌릴 것.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field as dc_field

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import openpyxl                                                    # noqa: E402

from src.parsers.text.composite import CompositeIndex              # noqa: E402
from src.parsers.text.excel import parse_excel                     # noqa: E402
from src.parsers.text.field_index import FieldIndex, normalize_label  # noqa: E402
from src.parsers.text.pdf_text import parse_pdf_text               # noqa: E402

SHEET = "라벨링"
SKIP_VALUES = {"N/A", "NA", "판독불가", ""}      # 정답이 없는 칸 — 채점 제외
EXCEL_EXT = {".xlsx", ".xlsm"}


@dataclass
class Cell:
    doc_id: str
    field_key: str
    field_name: str
    truth: str
    uncertain: bool              # 정답 앞에 '?' 가 붙어 있었다
    got: str | None = None
    verdict: str = "미추출"       # 정확 / 표기차이 / 정규화대기 / 오답 / 미추출


@dataclass
class Score:
    cells: list[Cell] = dc_field(default_factory=list)
    docs: list[tuple[str, str, str]] = dc_field(default_factory=list)   # id, 파일, 상태
    unscorable_fields: list[str] = dc_field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out = {"정확": 0, "표기차이": 0, "정규화대기": 0, "오답": 0, "미추출": 0}
        for c in self.cells:
            out[c.verdict] += 1
        return out


def _norm(v: object) -> str:
    return re.sub(r"\s+", " ", str(v or "").strip()).upper()


def _loose(v: object) -> str:
    return re.sub(r"[^0-9A-Z]", "", str(v or "").upper())


def read_kit(path: str, ix: FieldIndex) -> tuple[list[dict], list[tuple[int, str]], list[str]]:
    """킷 → (문서 행 목록, 채점 가능한 (열, field_key) 목록, 대응 미확정 필드명)"""
    ws = openpyxl.load_workbook(path, data_only=True)[SHEET]

    cols, unresolved, cur = [], [], None
    for c in range(10, ws.max_column + 1):
        h1, h2 = ws.cell(1, c).value, ws.cell(2, c).value
        if h1:
            cur = str(h1).replace("※안전", "").strip().lstrip("· ").strip()
        if h2 != "정답값" or not cur:
            continue
        hit = ix.lookup(cur)
        if hit is None:
            unresolved.append(cur)          # 킷 이름이 스키마에 없다 (예: RATED CV)
        else:
            cols.append((c, hit.key, hit.name))

    rows = []
    for r in range(3, ws.max_row + 1):
        fn = ws.cell(r, 2).value
        if not fn:
            continue
        rows.append({
            "row": r,
            "doc_id": str(ws.cell(r, 1).value or ""),
            "file": str(fn).strip(),
            "fmt": str(ws.cell(r, 3).value or ""),
            "cls": str(ws.cell(r, 5).value or ""),
            "spec_page": ws.cell(r, 7).value,
            "truth": {key: (ws.cell(r, c).value, name) for c, key, name in cols},
        })
    return rows, cols, unresolved


def find_file(root: str, name: str) -> str | None:
    for dirpath, _, files in os.walk(root):
        if name in files:
            return os.path.join(dirpath, name)
    return None


def score(kit_path: str, root: str) -> Score:
    ix, cix = FieldIndex.load(), CompositeIndex.load()
    rows, _, unresolved = read_kit(kit_path, ix)
    sc = Score(unscorable_fields=unresolved)

    for row in rows:
        path = find_file(root, row["file"])
        if row["cls"] == "out_of_scope":
            sc.docs.append((row["doc_id"], row["file"], "제외(out_of_scope)"))
            continue
        if path is None:
            sc.docs.append((row["doc_id"], row["file"], "파일 없음"))
            continue

        ext = os.path.splitext(path)[1].lower()
        page = row["spec_page"]
        try:
            if ext in EXCEL_EXT:
                res = parse_excel(path, index=ix, composite=cix,
                                  sheets=[int(page)] if page else None)
            elif ext == ".pdf":
                res = parse_pdf_text(path, index=ix, composite=cix,
                                     pages=[int(page)] if page else None)
            else:
                sc.docs.append((row["doc_id"], row["file"], f"{ext} 미지원 — VLM 담당"))
                continue
        except Exception as e:
            sc.docs.append((row["doc_id"], row["file"], f"파싱 실패: {e}"))
            continue

        got = {r.field_key: r.raw_value for r in res.records if r.found}
        sc.docs.append((row["doc_id"], row["file"], "채점"))

        for key, (truth, name) in row["truth"].items():
            t = str(truth or "").strip()
            uncertain = t.startswith("?")
            if uncertain:
                t = t[1:].strip()
            if _norm(t) in SKIP_VALUES:
                continue
            g = got.get(key)
            cell = Cell(row["doc_id"], key, name, t, uncertain, g)
            if g is None:
                cell.verdict = "미추출"
            elif _norm(g) == _norm(t):
                cell.verdict = "정확"
            elif _loose(g) == _loose(t):
                cell.verdict = "표기차이"
            elif _loose(g) and _loose(g) in _loose(t):
                # 파서는 문서 원문을 낸다. 표준값으로 바꾸는 것은 ④ Normalize 몫.
                #   예) 문서 "Fail Position: OPEN" → 파서 "OPEN" → 표준값 "FAIL OPEN"
                cell.verdict = "정규화대기"
            else:
                cell.verdict = "오답"
            sc.cells.append(cell)
    return sc


def render(sc: Score) -> str:
    n = sc.counts()
    total = sum(n.values())
    L = ["# 텍스트 파서 채점 (골든셋 대비)", ""]
    L += [f"- 채점 칸 **{total}개** · 정확 {n['정확']} · 표기차이 {n['표기차이']} "
          f"· 정규화대기 {n['정규화대기']} · 오답 {n['오답']} · 미추출 {n['미추출']}"]
    if total:
        grabbed = n["정확"] + n["표기차이"] + n["정규화대기"]
        L += [f"- **파서 관점 성공률 {grabbed / total * 100:.0f}%** — 맞는 칸을 집었는가. "
              f"파서의 책임 범위는 여기까지다",
              f"- 완전 일치율 {(n['정확'] + n['표기차이']) / total * 100:.0f}% — "
              f"표준값 변환까지 끝난 상태. 변환은 ④ Normalize 몫"]
    L += [""]

    if sc.unscorable_fields:
        L += ["> 채점 제외 — 킷 이름이 스키마에 없어 대조 불가: "
              + ", ".join(f"`{x}`" for x in sc.unscorable_fields), ""]

    L += ["## 문서별", "", "| 문서 | 파일 | 상태 |", "|---|---|---|"]
    for d, f, st in sc.docs:
        L.append(f"| {d} | {f} | {st} |")

    L += ["", "## 칸별", "", "| 문서 | 필드 | 정답 | 파서 출력 | 판정 |", "|---|---|---|---|---|"]
    for c in sorted(sc.cells, key=lambda x: (x.doc_id, x.field_key)):
        mark = " ⚠" if c.uncertain else ""
        L.append(f"| {c.doc_id} | {c.field_name} | {c.truth}{mark} | "
                 f"{c.got if c.got is not None else '—'} | {c.verdict} |")
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="골든셋 대비 텍스트 파서 채점")
    ap.add_argument("--kit", required=True)
    ap.add_argument("--root", default="raw_file")
    ap.add_argument("--out", default="runs/parser_score.md")
    a = ap.parse_args(argv)

    sc = score(os.path.expanduser(a.kit), a.root)
    md = render(sc)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(md)
    n = sc.counts()
    print(f"채점 칸 {sum(n.values())} · " + " · ".join(f"{k} {v}" for k, v in n.items()))
    print(f"  {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
