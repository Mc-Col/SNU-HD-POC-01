# -*- coding: utf-8 -*-
"""텍스트 파서 fixture 의 기대출력을 현재 스키마로 다시 만든다.

    python tools/refresh_text_fixtures.py            차이만 보여준다 (파일 안 씀)
    python tools/refresh_text_fixtures.py --write     확인 후 반영

왜 필요한가
─────────────────────────────────────────────────────────────
`fixtures/text/*.expected.json` 은 파서가 내야 할 값을 손으로 적어둔 것이다.
필드 표준이 바뀌면(필드 신설·삭제·병합) 그 기대값이 낡아 테스트가 깨진다.
코드 문제가 아니라 **기대값이 옛 스키마인** 상황이다.

    2026-08-24 실제로 일어난 것
      valve_body_type   신설  → 이제 매핑된다. 기대값은 unmapped 로 적혀 있었다
      positioner_type   삭제  → 기대값에 아직 남아 있었다
      rated_cv          병합  → 미매핑 컬럼으로 기대되어 있었다

⚠️ 이 도구는 **차이를 보여주는 것이 본업**이다. `--write` 없이 먼저 돌려
   무엇이 어떻게 바뀌는지 눈으로 확인하고, 의도한 변경일 때만 반영한다.
   확인 없이 덮어쓰면 테스트가 "파서가 낸 값 = 정답" 이 되어 아무것도 잡지
   못한다. 그것이 스냅샷 테스트의 유일한 함정이다.

이 도구로 해결되지 않는 것
─────────────────────────────────────────────────────────────
전제 자체가 사라진 테스트는 기대값을 고쳐도 안 된다. 사람이 판단해야 한다.
`--write` 후에도 남는 실패가 있으면 그쪽이다 (실행 끝에 안내한다).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

sys.stdout.reconfigure(encoding="utf-8")

REC_KEYS = ("field_key", "raw_value", "raw_label", "source_locator",
            "page", "confidence", "note")
UNM_KEYS = ("text", "source_locator", "neighbor_value")

# (기대값 파일, 입력 파일, 파서, 추가 인자)
TARGETS = [
    ("fixtures/text/excel_basic.expected.json", "fixtures/text/excel_basic.xlsx",
     "excel", {}),
    ("fixtures/text/excel_layouts.expected.json", "fixtures/text/excel_layouts.xlsx",
     "excel", {}),
    ("fixtures/text/pdf_basic.expected.json", "fixtures/text/pdf_basic.pdf",
     "pdf", {}),
]


def build(kind: str, path: str, **kw) -> dict:
    from src.parsers.text.excel import parse_excel
    from src.parsers.text.pdf_text import parse_pdf_text
    res = parse_excel(path, **kw) if kind == "excel" else parse_pdf_text(path, **kw)
    return {
        "records": [{k: getattr(r, k) for k in REC_KEYS} for r in res.records],
        "unmapped": [{k: getattr(u, k) for k in UNM_KEYS} for u in res.unmapped],
    }


def diff(old: dict, new: dict) -> list[str]:
    """읽을 수 있는 차이. 필드 키 기준으로 맞춰 본다."""
    out = []
    for sect, idkey in (("records", "field_key"), ("unmapped", "text")):
        o = {str(x.get(idkey)): x for x in (old.get(sect) or [])}
        n = {str(x.get(idkey)): x for x in (new.get(sect) or [])}
        for k in sorted(set(n) - set(o)):
            v = n[k].get("raw_value", n[k].get("neighbor_value"))
            out.append(f"  + [{sect}] {k} = {v!r}")
        for k in sorted(set(o) - set(n)):
            v = o[k].get("raw_value", o[k].get("neighbor_value"))
            out.append(f"  - [{sect}] {k} = {v!r}  (기대값에 있었으나 이제 안 나온다)")
        for k in sorted(set(o) & set(n)):
            for f in (REC_KEYS if sect == "records" else UNM_KEYS):
                a, b = o[k].get(f), n[k].get(f)
                if a != b:
                    out.append(f"  ~ [{sect}] {k}.{f}: {a!r} → {b!r}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="텍스트 파서 fixture 기대출력 재생성")
    ap.add_argument("--write", action="store_true", help="확인 후 파일에 반영")
    a = ap.parse_args(argv)

    changed = 0
    for exp_path, src_path, kind, kw in TARGETS:
        full_exp = os.path.join(ROOT, exp_path)
        if not os.path.exists(full_exp):
            print(f"건너뜀 — 기대값 파일 없음: {exp_path}")
            continue
        with open(full_exp, encoding="utf-8") as f:
            old = json.load(f)
        try:
            new = build(kind, os.path.join(ROOT, src_path), **kw)
        except Exception as e:
            print(f"★ 실패 {exp_path}: {type(e).__name__} {e}")
            continue

        d = diff(old, new)
        print(f"\n── {exp_path}")
        if not d:
            print("  차이 없음")
            continue
        changed += 1
        for line in d:
            print(line)
        if a.write:
            merged = dict(old)                 # `_설명` 등 주석 키를 보존한다
            merged["records"], merged["unmapped"] = new["records"], new["unmapped"]
            with open(full_exp, "w", encoding="utf-8", newline="\n") as f:
                json.dump(merged, f, ensure_ascii=False, indent=2)
                f.write("\n")
            print("  → 반영")

    print()
    if not a.write:
        print(f"차이가 있는 파일 {changed}개. 위 내용이 의도한 변경이면 "
              f"`python tools/refresh_text_fixtures.py --write` 로 반영한다.")
    else:
        print("반영 완료. 이제 테스트를 돌린다: python -m pytest src/parsers/text -q")
        print()
        print("남는 실패는 **전제가 사라진 테스트**다 — 기대값이 아니라 테스트 자체를")
        print("고쳐야 한다. 사람이 판단할 몫이므로 이 도구가 손대지 않는다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
