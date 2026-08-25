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
from src.parsers.text.sections import SectionIndex                 # noqa: E402
from src.parsers.text.pdf_text import parse_pdf_text               # noqa: E402
from src.parsers.text.units import UnitIndex                       # noqa: E402

# 값 대조는 팀의 평가 하네스 규칙을 그대로 쓴다 (단위 표기·로마자·대소문자).
# 여기서 다시 만들면 채점기와 하네스가 서로 다른 답을 내놓는다.
from eval.compare import same as _same                             # noqa: E402
from src import schema as _schema                                  # noqa: E402
from src.parsers.text.crosscheck import numeric_flag as _numeric   # noqa: E402


def _standardize(field_key: str, value: object) -> str:
    """schema/rules.yaml 의 표기 매핑을 적용한 값.

    킷은 FAIL ACTION 만 표준값으로 적게 되어 있다(킷 기입 안내). 파서는 문서
    원문(`VALVE CLOSE`)을 내므로, 사전을 적용하지 않으면 영원히 오답으로 잡힌다.
    """
    raw = str(value or "").strip()
    probe = _schema.norm_label(raw)
    for m in _schema.value_aliases(field_key):
        if probe in {_schema.norm_label(c) for c in m.get("from", [])}:
            return str(m["to"])
    return raw

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
    labeler: str = ""            # 이 정답을 만든 사람 — 집계를 나누기 위해


@dataclass
class Score:
    cells: list[Cell] = dc_field(default_factory=list)
    docs: list[tuple[str, str, str]] = dc_field(default_factory=list)   # id, 파일, 상태
    unscorable_fields: list[str] = dc_field(default_factory=list)

    def counts(self, labeler: str | None = None) -> dict[str, int]:
        """판정별 개수. labeler 를 주면 그 사람이 만든 정답만 센다."""
        out = {"정확": 0, "표기차이": 0, "정규화대기": 0, "오답": 0, "미추출": 0}
        for c in self.cells:
            if labeler is not None and c.labeler != labeler:
                continue
            out[c.verdict] += 1
        return out

    def labelers(self) -> list[str]:
        """등장 순서대로의 라벨러 목록 (같은 입력 → 같은 출력)."""
        seen: list[str] = []
        for c in self.cells:
            if c.labeler not in seen:
                seen.append(c.labeler)
        return seen


def _cell_text(v: object) -> str:
    """킷 셀 값을 문자로. 엑셀 왕복으로 숫자가 된 값을 되살린다.

    사람이 킷을 엑셀에서 열어 저장하면 텍스트 "195" 가 숫자 195 로 바뀌고
    openpyxl 은 195.0 으로 읽는다. 그대로 대조하면 `195.0` vs 문서의
    `195 (Cg=4040 → 7580)` 이 달라져 맞는 값이 오답으로 잡힌다
    (2026-08-25 실제로 발생). 정수인 실수는 소수점을 떼고 본다.
    """
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v or "").strip()


def _norm(v: object) -> str:
    return re.sub(r"\s+", " ", str(v or "").strip()).upper()


def _loose(v: object) -> str:
    return re.sub(r"[^0-9A-Z]", "", str(v or "").upper())


def _contains(outer: object, inner: object) -> bool:
    """한쪽이 다른 쪽을 통째로 담고 있는가 (공백·기호 무시)."""
    a, b = _loose(outer), _loose(inner)
    return bool(a) and bool(b) and b in a


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
            "labeler": str(ws.cell(r, 8).value or "").strip() or "미기재",
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
    six = SectionIndex.load()
    # 구역 접두어를 뗀 표기까지 쓰는 인덱스로 채점한다 — 파서가 실제로 쓰는 것과 같게
    ix, cix = FieldIndex.load(section_names=six.name_map()), CompositeIndex.load()
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
            t = _cell_text(truth)
            uncertain = t.startswith("?")
            if uncertain:
                t = t[1:].strip()
            if _norm(t) in SKIP_VALUES:
                continue
            g = got.get(key)
            cell = Cell(row["doc_id"], labeler=row["labeler"], field_key=key,
                        field_name=name, truth=t, uncertain=uncertain, got=g)
            if g is None:
                cell.verdict = "미추출"
            elif _norm(g) == _norm(t):
                cell.verdict = "정확"
            elif _loose(g) == _loose(t) or _same(t, g, _numeric(key)):
                # 단위 표기·로마자·대소문자 차이는 감점하지 않는다 (eval/compare 규칙).
                #   예) 정답 "160 ℃" vs 파서 "160.0" → 같은 값이다
                cell.verdict = "표기차이"
            elif (_same(t, _standardize(key, g), _numeric(key))
                  or _contains(g, t) or _contains(t, g)):
                # 표기 매핑 사전(rules.yaml)을 거쳐야 같아지는 것도 여기다.
                # 파서는 문서 원문을 낸다. 표준값으로 바꾸는 것은 ④ Normalize 몫.
                #   문서가 더 짧을 수도(원문 "OPEN" → 표준 "FAIL OPEN"),
                #   더 길 수도 있다(원문 "195 (Cg=4040)" → 표준 "195").
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

    # ── 라벨러별 (2026-08-26) ────────────────────────────────────
    #   골든셋에 AI 초안이 섞여 있다. AI 가 만든 정답으로 AI 를 채점하면 순환이라,
    #   숫자를 하나로 합치면 발표에서 방어할 수 없다. 나눠서 낸다.
    labelers = sc.labelers()
    if len(labelers) > 1:
        L += ["", "## 라벨러별", "",
              "| 라벨러 | 칸 | 정확 | 표기차이 | 정규화대기 | 오답 | 미추출 | 성공률 |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
        for who in labelers:
            m = sc.counts(who)
            t = sum(m.values())
            ok = m["정확"] + m["표기차이"] + m["정규화대기"]
            L.append(f"| {who} | {t} | {m['정확']} | {m['표기차이']} | {m['정규화대기']} | "
                     f"{m['오답']} | {m['미추출']} | {ok / t * 100:.0f}% |" if t else
                     f"| {who} | 0 | | | | | | — |")
        human = [w for w in labelers if "AI" not in w]
        if human and len(human) < len(labelers):
            hm = {k: sum(sc.counts(w)[k] for w in human) for k in
                  ("정확", "표기차이", "정규화대기", "오답", "미추출")}
            ht = sum(hm.values())
            hok = hm["정확"] + hm["표기차이"] + hm["정규화대기"]
            L += ["", f"**사람이 검증한 정답만**: {ht}칸 · 성공률 "
                      f"**{hok / ht * 100:.0f}%** · 오답 {hm['오답']}건" if ht else ""]
            L += ["", "> AI 초안은 사람 검증 전이다. 그 행을 기준으로 잰 숫자는 "
                      "**AI 가 만든 정답으로 AI 를 채점**하는 부분이 섞이므로 참고로만 본다.",
                  "> 텍스트 파서는 VLM 과 다른 방식이라 완전한 순환은 아니지만, "
                  "AI 가 놓친 값은 정답지에서도 N/A 로 남아 미추출이 과소평가될 수 있다."]
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
