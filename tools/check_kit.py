# -*- coding: utf-8 -*-
"""라벨링 킷을 규칙 파일과 대조한다.

    python tools/check_kit.py                 지적사항만
    python tools/check_kit.py --all           통과 항목도

무엇을 보나
─────────────────────────────────────────────────────────────
① **정답이 표준값인가** — 킷에 `CLASS IV` 가 적혀 있으면 추출값 `CLASS 4`
   와 대조할 때 채점이 흔들린다. 정답지가 먼저 표준을 지켜야 한다.
② **허용 어휘 안인가** — 어휘 밖 값은 오기이거나, 어휘가 아직 좁은 것이다.
   둘 중 무엇인지는 사람이 판단한다.
③ **파일·페이지·태그 정합성**

왜 도구로 만드나
─────────────────────────────────────────────────────────────
킷은 계속 자란다. 눈으로 보는 검토는 행이 늘수록 빠진 곳이 생기고,
**정답지의 오류는 이후 모든 측정을 조용히 왜곡한다**(인사이트 48).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 리포트는 한글이다. Windows 에서 출력을 리다이렉트하면 콘솔 코드페이지가
# 쓰여 대시 문자에서 죽는다 — 결과를 다 만든 뒤 마지막에 죽으므로
# 성공한 실행이 실패로 보인다.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from eval import compare                                    # noqa: E402
from eval.kit import locate, read_kit                       # noqa: E402
from src import preprocess as pp                            # noqa: E402
from src import schema                                      # noqa: E402
from src.contracts import ParserType, RawExtraction         # noqa: E402
from src.pipeline import DefaultNormalize                   # noqa: E402

NORM = DefaultNormalize()


def _inches(v):
    """치수 표기를 인치 실수로. 분수·혼합분수를 받는다."""
    t = str(v or "").strip().replace('"', "")
    m = re.match(r"^(\d+)-(\d+)/(\d+)$", t)
    if m:
        return int(m[1]) + int(m[2]) / int(m[3])
    m = re.match(r"^(\d+)/(\d+)$", t)
    if m:
        return int(m[1]) / int(m[2])
    m = re.match(r"^([\d.]+)$", t)
    return float(m[1]) if m else None


def _lead_num(v):
    """앞머리 숫자만. 단위가 붙어 있어도 읽는다."""
    m = re.match(r"^([\d.]+)", str(v or "").strip().replace(",", ""))
    return float(m[1]) if m else None


def _standard(key: str, value: str, label: str = "") -> str:
    """이 값을 정규화하면 무엇이 되나 — 정답지가 표준값인지 보는 데 쓴다.

    **원문라벨을 함께 넘긴다.** 페일액션처럼 라벨에 따라 결과가 뒤집히는
    규칙이 있어서, 라벨 없이 부르면 그 규칙이 돌지 않고 차이를 놓친다.
    """
    ex = RawExtraction(field_key=key, raw_value=value, raw_label=label,
                       parser=ParserType.VLM, confidence=1.0)
    return NORM.run(ex, schema.get(key))[0]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kit", default="readme/labeling_kit.xlsx")
    ap.add_argument("--root", default="raw_file")
    ap.add_argument("--holdout", default="d036,d037,d038,d039,d040",
                    help="블라인드 홀드아웃 문서ID. 일반 점검에서 빼고 따로 낸다. "
                         "빈 문자열이면 분리하지 않는다")
    ap.add_argument("--all", action="store_true", help="통과 항목도 보인다")
    a = ap.parse_args(argv)

    rows = read_kit(a.kit)
    missing = locate(rows, a.root)
    filled = [r for r in rows
              if any(str(v).strip() for v in r.truth.values())]

    print(f"킷 {len(rows)}행 · 채워진 것 {len(filled)}행")
    if read_kit.unmatched:
        print(f"⚠ 스키마에 없는 열: {', '.join(read_kit.unmatched)}")
    if missing:
        print(f"⚠ 파일을 찾지 못함: {', '.join(missing)}")

    n_std = n_vocab = n_tag = 0
    hold_ids = {x.strip() for x in (a.holdout or "").split(",") if x.strip()}
    holdout = [r for r in filled if r.doc_id in hold_ids]
    filled = [r for r in filled if r.doc_id not in hold_ids]
    if holdout:
        print(f"🔒 홀드아웃 {len(holdout)}건을 일반 점검에서 뺐다 — "
              f"{', '.join(r.doc_id for r in holdout)}")

    print("\n── ① 정답이 표준값과 다른 칸 " + "─" * 40)
    for r in filled:
        for f in schema.all_fields():
            v = str(r.truth.get(f.key) or "").strip()
            if not v or compare.is_na(v):
                continue
            std = _standard(f.key, v, (r.raw_label or {}).get(f.key, ''))
            if std is not None and str(std) != v:
                n_std += 1
                print(f"   {r.doc_id}  {f.key:26s} {v!r}  →  {std!r}")
    if not n_std:
        print("   없음")

    print("\n── ② 허용 어휘 밖 값 " + "─" * 46)
    seen = Counter()
    for r in filled:
        for f in schema.all_fields():
            v = str(r.truth.get(f.key) or "").strip()
            if not v or compare.is_na(v):
                continue
            if schema.in_vocabulary(f.key, v) is False:
                n_vocab += 1
                seen[(f.key, v)] += 1
    for (key, v), n in seen.most_common():
        docs = [r.doc_id for r in filled
                if str(r.truth.get(key) or "").strip() == v]
        print(f"   {n:2d}건  {key:26s} {v!r:26s} {','.join(docs[:6])}")
    if not seen:
        print("   없음")

    print("\n── ③ 파일명 태그와 기입 태그 " + "─" * 38)
    for r in filled:
        fn = pp.parse_filename(r.file).tag if r.file else None
        got = str(r.truth.get("engineering_tag_no") or "").strip()
        if not fn or not got:
            if a.all:
                print(f"   {r.doc_id}  파일명에 태그 없음 — 판정하지 않음")
            continue
        if pp.normalize_tag(fn) != pp.normalize_tag(got):
            n_tag += 1
            print(f"   {r.doc_id}  파일명 {fn} ≠ 기입 {got}")
    if not n_tag:
        print("   없음")

    # ④ 유량계수가 밸브 크기에 비해 말이 되는가.
    #    Cv 는 대체로 크기의 제곱에 비례한다 — 글로브는 약 10×d², 버터플라이도
    #    30×d² 를 크게 넘지 않는다. 이 비가 수십 배로 뛰면 그 값은 Cv 가 아니라
    #    **Cg** 다(Cg = C1 × Cv, C1 은 대개 33~37).
    #
    #    실제로 골든셋 5건이 Cg 를 rated_cv 에 담고 있었다. 셀 정합성 검사로는
    #    하나도 걸리지 않았다 — 숫자로서는 완벽히 정상이었기 때문이다.
    #    **물리로 재야 보인다.**
    print("\n── ④ 유량계수가 크기에 비해 말이 되는가 " + "─" * 27)
    n_cv = 0
    ratios = []
    for r in filled:
        d = _inches(r.truth.get("valve_body_size"))
        cv = _lead_num(r.truth.get("rated_cv"))
        if not d or not cv:
            continue
        ratio = cv / (d * d)
        size = r.truth.get("valve_body_size")
        if ratio > 60:
            n_cv += 1
            print(f"   {r.doc_id}  {size} 에 Cv {cv:.0f} → {ratio:.0f}×d²"
                  f"  🔴 Cg 로 의심된다 (÷35 하면 {ratio / 35:.0f}×d²)")
        elif ratio > 35:
            n_cv += 1
            print(f"   {r.doc_id}  {size} 에 Cv {cv:.0f} → {ratio:.0f}×d²"
                  f"  ⚠ 원문 확인 필요")
        else:
            ratios.append(ratio)
    if not n_cv:
        print("   없음")
    if ratios:
        ratios.sort()
        print(f"   (정상 {len(ratios)}건 — 중앙 {ratios[len(ratios) // 2]:.0f}×d²"
              f" · 최대 {ratios[-1]:.0f}×d²)")

    if holdout:
        print("\n── 🔒 홀드아웃 (참고만) " + "─" * 42)
        print("   ⚠ **이 결과로 어휘를 넓히거나 규칙을 고치면 홀드아웃이 탄다.**")
        print("     표기 통일(대소문자)은 라벨러가 해도 새지 않는다.")
        print("     어휘를 늘리는 것은 샌다 — 개봉 후에 한다.")
        n_h = 0
        for r in holdout:
            for f in schema.all_fields():
                v = str(r.truth.get(f.key) or "").strip()
                if not v or v.upper() in ("NA", "N/A"):
                    continue
                std = _standard(f.key, v, (r.raw_label or {}).get(f.key, ""))
                if std is not None and str(std) != v:
                    print(f"   {r.doc_id}  {f.key:24s} {v!r} → {std!r}  (표기)")
                    n_h += 1
        if not n_h:
            print("   표기 불일치 없음")

    print("\n" + "─" * 66)
    print(f"표준값 불일치 {n_std}칸 · 어휘 밖 {n_vocab}칸 "
          f"({len(seen)}종) · 태그 불일치 {n_tag}건 · 유량계수 이상 {n_cv}건")
    print("어휘 밖이 전부 오기는 아니다 — 어휘가 아직 좁을 수 있다. "
          "승인하면 어휘가 자란다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
