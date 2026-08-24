# -*- coding: utf-8 -*-
"""골든셋 후보 추천 — 빈 축을 채울 파일을 고른다

    python tools/pick_golden_candidates.py

왜 필요한가
─────────────────────────────────────────────────────────────
골든셋 11건의 문제는 개수가 아니라 **편중**이다. 실측(2026-08-24):

    FV 380건 → 골든셋 6건 (과대표집)
    XV  91건 → 0건 ★     TV 68건 → 0건 ★     HV 26건 → 0건 ★
    PCV 74건 → 1건 (레귤레이터 결정이 표본 1건에 걸려 있다)

무작위로 더 채우면 FV 가 또 뽑힌다. 그래서 **비어 있는 축을 지정해서** 뽑는다.

코퍼스 스캔은 기계가, 판단은 사람이 — 이 도구는 후보만 낸다.
정답을 정하지 않고, 이미 골든셋에 있는 파일은 제외한다.
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

sys.stdout.reconfigure(encoding="utf-8")

from eval.kit import locate, read_kit          # noqa: E402
from src import preprocess as pre              # noqa: E402

# 레귤레이터 표기 — 에어셋 부속품·양식 상용구는 걸러낸다(eval/groups.py 와 같은 규약)
NOISE = re.compile(r"FILTER\s*/?\s*REGULATOR|AIR\s*REGULATOR|REGULATOR\s*/?\s*FILTER"
                   r"|VALVE\s*/\s*REGULATOR\s*SIZING|6\d[A-Z]{2,3}R", re.I)
REG = re.compile(r"\bREGULATOR\b|DIRECT[\s-]*OPERATED|SELF[\s-]*OPERATED"
                 r"|PRESSURE\s*REDUCING", re.I)
VENDOR = [
    ("FISHER", r"FISHER"), ("METSO", r"METSO"), ("VALSTONE", r"VALSTONE"),
    ("MASONEILAN", r"MASONEILAN"), ("FLOWSERVE", r"FLOWSERVE"),
    ("UNICON", r"UNICON"), ("HONEYWELL", r"HONEYWELL"), ("AZBIL", r"AZBIL|YAMATAKE"),
    ("SAMSON", r"SAMSON"), ("KOSO", r"\bKOSO\b"), ("YTC", r"\bY\.?T\.?C\b"),
    ("JUNG", r"JUNG\s*ENGINEER|정엔지니어링"),
]

# 채우고 싶은 축 — (설비종류, 몇 건, 이유)
WANT = [
    ("XV", 2, "코퍼스 91건인데 골든셋 0건. On-off 밸브라 필드 구성이 다를 수 있다"),
    ("TV", 1, "코퍼스 68건인데 0건"),
    ("HV", 1, "코퍼스 26건인데 0건"),
    ("PDV", 1, "차압 밸브. 0건"),
]
WANT_REG = 2       # 레귤레이터 — PCV 결정 근거를 1건 → 3건으로
WANT_MULTI = 2     # 사양표가 p1 이 아닐 가능성이 있는 다중 페이지 문서


def probe(path: str) -> dict:
    """열어보고 사실만 모은다. 실패는 삼키지 않고 사유를 남긴다."""
    out = {"pages": None, "text_pages": 0, "vendor": "", "regulator": False,
           "err": ""}
    try:
        pt = pre.probe_pages(path)
    except Exception as e:
        out["err"] = f"{type(e).__name__}"
        return out
    out["pages"] = len(pt)
    out["text_pages"] = sum(1 for x in pt if x.has_text_layer)
    blob = " ".join(x.text for x in pt)
    if len(blob.strip()) >= 100:
        clean = NOISE.sub(" ", blob)
        out["regulator"] = bool(REG.search(clean))
        for name, pat in VENDOR:
            if re.search(pat, blob, re.I):
                out["vendor"] = name
                break
    return out


def main() -> int:
    rows = read_kit(os.path.join(ROOT, "readme", "labeling_kit.xlsx"))
    locate(rows, os.path.join(ROOT, "raw_file"))
    used = {(r.file or "").lower() for r in rows}
    used_vendors = {(r.truth.get("manufacturer") or "").upper() for r in rows}

    # 대상 파일 — 최상위만(조원 배분 폴더는 사본이다)
    files = []
    for f in sorted(os.listdir(os.path.join(ROOT, "raw_file"))):
        p = os.path.join(ROOT, "raw_file", f)
        if not os.path.isfile(p) or f.lower() in used:
            continue
        i = pre.parse_filename(p)
        if i.in_scope is not True or not i.tag_parts:
            continue
        files.append((p, f, i))

    print(f"대상 {len(files)}건에서 후보를 고른다 "
          f"(골든셋 {len(rows)}건과 조원 배분 사본은 제외)\n")

    picked: list[tuple[str, str, str, dict]] = []
    seen = set()

    def take(reason, p, f, info, pr):
        if f.lower() in seen:
            return False
        seen.add(f.lower())
        picked.append((reason, f, info.tag_parts.kind, pr))
        return True

    # ── ① 미대표 설비종류 ─────────────────────────────────────
    for kind, n, why in WANT:
        cand = [(p, f, i) for p, f, i in files if i.tag_parts.kind == kind]
        # tif 를 우선한다 — 코퍼스의 71.9% 이고 VLM 경로를 재는 표본이 된다
        cand.sort(key=lambda x: (x[2].ext != ".tif", x[1]))
        got = 0
        for p, f, i in cand:
            if got >= n:
                break
            pr = probe(p)
            if pr["err"] or not pr["pages"]:
                continue
            if take(f"{kind} 미대표 — {why}", p, f, i, pr):
                got += 1

    # ── ② 레귤레이터 ──────────────────────────────────────────
    got = 0
    for p, f, i in files:
        if got >= WANT_REG:
            break
        if i.tag_parts.kind != "PCV" or i.ext != ".pdf":
            continue                     # 텍스트로 레귤레이터 여부를 확인할 수 있는 것
        pr = probe(p)
        if pr["regulator"] and take(
                "레귤레이터 — PCV 결정 근거가 표본 1건뿐이다", p, f, i, pr):
            got += 1

    # ── ③ 사양표가 p1 이 아닐 가능성 ──────────────────────────
    got = 0
    for p, f, i in files:
        if got >= WANT_MULTI:
            break
        if i.ext != ".tif":
            continue
        pr = probe(p)
        if (pr["pages"] or 0) >= 6 and take(
                "다중 페이지 — 자동 페이지 선택을 잴 표본이 d004 뿐이다",
                p, f, i, pr):
            got += 1

    # ── ④ 새 벤더 ─────────────────────────────────────────────
    for p, f, i in files:
        if i.ext != ".pdf":
            continue
        pr = probe(p)
        v = pr["vendor"]
        if v and v not in used_vendors:
            if take(f"새 벤더 {v} — 골든셋에 없다", p, f, i, pr):
                used_vendors.add(v)
        if len([1 for r, *_ in picked if r.startswith("새 벤더")]) >= 2:
            break

    # ── 출력 ──────────────────────────────────────────────────
    print(f"{'파일명':<44}{'종류':<6}{'p':>3} {'텍스트':>5}  {'벤더':<11}이유")
    print("-" * 128)
    for reason, f, kind, pr in picked:
        t = f"{pr['text_pages']}/{pr['pages']}"
        print(f"{f[:43]:<44}{kind:<6}{pr['pages'] or 0:>3} {t:>5}  "
              f"{(pr['vendor'] or '—'):<11}{reason}")

    print(f"\n후보 {len(picked)}건. 라벨링 킷의 빈 행(d012~)에 파일명을 적고 채운다.")
    print("주의 — 이 도구는 후보만 낸다. 사양표 페이지와 정답값은 사람이 판단한다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
