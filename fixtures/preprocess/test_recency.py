# -*- coding: utf-8 -*-
"""최신성 판정 자체 검증 — 10FV011 실물에서 나온 규칙

    python fixtures/preprocess/test_recency.py

이 파일이 지키는 것: `10FV011-DATA SHEET_REV1.tif` 에서 p4(1986 "OLD")를
고르면 MODEL NO. 와 RATED CV 가 틀린다. 그 회귀를 막는다.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.stdout.reconfigure(encoding="utf-8")

from src.contracts import PageClass, PageInfo
from src.preprocess import find_marks, parse_doc_date, pick_latest_spec

ok = fail = 0


def check(label: str, got, want) -> None:
    global ok, fail
    if got == want:
        ok += 1
        print(f"  OK   {label}")
    else:
        fail += 1
        print(f"  실패 {label}\n         받음 {got!r}\n         기대 {want!r}")


def spec(page, date="", marker="", superseded=False, tags=(), amb=False):
    d = parse_doc_date(date)
    return PageInfo(page=page, page_class=PageClass.SPEC, doc_date=d.raw or date,
                    date_key=d.key if d else None, date_ambiguous=d.ambiguous or amb,
                    revision_marker=marker, superseded=superseded, tags=list(tags))


# ══ 1. 날짜 읽기 ══════════════════════════════════════════════════
print("\n[1] 날짜 읽기 — 4자리 연도가 없으면 날짜로 보지 않는다")
check("2003/03/25 (연도 먼저)", parse_doc_date("Date 2003/03/25").key, (2003, 3, 25))
check("SEP. 11, 2015 (월 이름)", parse_doc_date("DATE SEP. 11, 2015").key, (2015, 9, 11))
check("9/6/1986 (연도 끝)", parse_doc_date("Date 9/6/1986").key, (1986, 9, 6))
check("9/6/1986 은 월·일 모호", parse_doc_date("9/6/1986").ambiguous, True)
check("11/1/85 은 날짜 아님(2자리 연도)", bool(parse_doc_date("QUOT 11/1/85")), False)
check("견적번호 85-1874 는 날짜 아님", bool(parse_doc_date("Quote No. 85-1874")), False)
check("REV1 은 날짜 아님", bool(parse_doc_date("REV1")), False)
check("팩스헤더 03-04 22:59 는 날짜 아님", bool(parse_doc_date("03-04 22:59 THU")), False)
# 실물 헤더 전체 — 견적번호와 날짜가 나란히 있다. 날짜만 잡혀야 한다
check("p4 헤더 전체에서 1986 만 잡힘",
      parse_doc_date("Order No. Quote No. 85-1874 Date 9/6/1986 Page").key,
      (1986, 9, 6))

# ══ 2. 표기 읽기 ══════════════════════════════════════════════════
print("\n[2] 개정·폐기 표기")
check("수기 OLD → 폐기", find_marks("OLD  P.10107-6"), ("OLD", True))
check("CONTROL VALVE RETROFIT", find_marks("Project CONTROL VALVE RETROFIT"),
      ("RETROFIT", False))
check("표기 없음", find_marks("Fisher Controls Control Valve Specification"),
      ("", False))
check("폐기가 개정보다 우선", find_marks("RETROFIT (OLD)"), ("OLD", True))
check("GOLD 는 OLD 아님", find_marks("GOLD PLATED")[1], False)

# ══ 3. 실물 — 10FV011 ════════════════════════════════════════════
print("\n[3] 10FV011-DATA SHEET_REV1.tif — 사양표 2장")
pages = [
    spec(1, "2003/03/25", "RETROFIT", tags=["A10FV011", "A10FV012", "A10FV013", "A10FV014"]),
    PageInfo(page=2, page_class=PageClass.DRAWING),
    PageInfo(page=3, page_class=PageClass.COVER),
    spec(4, "9/6/1986", "OLD", superseded=True, tags=["A10FV011"]),
    PageInfo(page=5, page_class=PageClass.DRAWING),
    PageInfo(page=6, page_class=PageClass.CALC),
    PageInfo(page=7, page_class=PageClass.BOM),
    PageInfo(page=8, page_class=PageClass.BOM),
]
got, why = pick_latest_spec(pages, file_tag="A10FV011")
check("p1(2003 Retrofit)을 고른다 — p4 가 아니다", got.page if got else None, 1)
print(f"       사유: {why}")

print("\n[3-b] 수기 OLD 를 못 읽었을 때도 날짜로 같은 답이 나오는가")
pages_nomark = [
    spec(1, "2003/03/25", "RETROFIT", tags=["A10FV011", "A10FV012"]),
    spec(4, "9/6/1986", tags=["A10FV011"]),
]
got, why = pick_latest_spec(pages_nomark, file_tag="A10FV011")
check("날짜만으로 p1", got.page if got else None, 1)
print(f"       사유: {why}")

print("\n[3-c] 태그 단독성으로 고르면 틀린다 (이전 규칙의 회귀 방지)")
solo = [p for p in pages_nomark if p.tags == ["A10FV011"]]
check("태그 단독 페이지는 p4 — 이것을 고르면 오답", solo[0].page, 4)

# ══ 4. 못 가리는 경우는 고르지 않는다 ═════════════════════════════
print("\n[4] 판정 불가 시 자동 선택하지 않는다")
got, why = pick_latest_spec([spec(1), spec(4)])
check("날짜·표기 모두 없음 → None", got, None)
print(f"       사유: {why}")

got, why = pick_latest_spec([spec(1, "1986/05/02", superseded=True, marker="OLD"),
                             spec(4, "1986/09/06", superseded=True, marker="OLD")])
check("후보 전부 폐기 → None", got, None)
print(f"       사유: {why}")

got, why = pick_latest_spec([spec(1, "3/5/2003", amb=True), spec(4, "5/3/2003", amb=True)])
check("같은 연도 + 월일 모호 → None", got, None)
print(f"       사유: {why}")

got, why = pick_latest_spec([spec(1, "2003/03/25", "RETROFIT"), spec(4, "2003/03/25")])
check("같은 날짜면 RETROFIT 쪽", got.page if got else None, 1)
print(f"       사유: {why}")

got, why = pick_latest_spec([PageInfo(page=1, page_class=PageClass.BOM)])
check("사양표 없음 → None", got, None)
print(f"       사유: {why}")

got, why = pick_latest_spec([spec(2)])
check("사양표 1장이면 날짜 없어도 확정", got.page if got else None, 2)
print(f"       사유: {why}")

# ══ 5. 태그의 역할 — 두 가지를 구분한다 ═══════════════════════════
#
#   같은 설비의 다른 시점 (태그 교집합 있음)  → 최신성으로 고른다.
#                                              태그 단독성으로 고르지 않는다.
#   서로 다른 설비      (태그 교집합 없음)  → 파일명 태그로 고른다.
#                                              최신성은 의미가 없다.
#
#   이 구분이 무너지면 C028(태그 단독성) 또는 C029(맹목적 최신성)로 되돌아간다.

print("\n[5-a] 같은 설비의 다른 시점 — 태그 단독성으로 고르지 않는다 (C028 회귀 방지)")
got, why = pick_latest_spec(
    [spec(1, "2003/03/25", "RETROFIT",
          tags=["A10FV011", "A10FV012", "A10FV013", "A10FV014"]),
     spec(4, "9/6/1986", tags=["A10FV011"])],
    file_tag="A10FV011")
check("태그가 단독인 p4 가 아니라 최신인 p1", got.page if got else None, 1)
print(f"       사유: {why}")

print("\n[5-b] 서로 다른 설비 — 파일명 태그로 고른다 (최신성 아님)")
# 070100_REV0.pdf 실물: p4 = B10-TV-040, p5 = B10-TV-1016. 견적서 한 건에
# 서로 다른 밸브 2대의 사양표가 각각 들어 있다. 최신성 문제가 아니다.
pages_multi = [spec(4, tags=["B10TV040"]), spec(5, tags=["B10TV1016"])]
got, why = pick_latest_spec(pages_multi, file_tag="B10TV1016")
check("파일명 태그와 일치하는 p5", got.page if got else None, 5)
print(f"       사유: {why}")

got, why = pick_latest_spec(pages_multi)
check("파일명에 태그가 없으면 고르지 않는다 → 자산 N건 발견", got, None)
check("사유에 자산 건수가 담긴다", "자산 2건" in why, True)
print(f"       사유: {why}")

got, why = pick_latest_spec(
    [spec(4, "2007/02/21", tags=["B10TV040"]),
     spec(5, "2007/02/21", tags=["B10TV1016"])])
check("날짜가 같아도 다른 설비면 최신성으로 풀지 않는다", got, None)

print("\n[5-c] 선택된 페이지에 파일명 태그가 없으면 호출자가 확인필요 처리")
got, why = pick_latest_spec(
    [spec(1, "2003/03/25", "RETROFIT", tags=["A10FV011", "A10FV099"]),
     spec(4, "9/6/1986", tags=["A10FV011"])],
    file_tag="A10FV011")
check("교집합이 있으므로 최신성으로 p1", got.page if got else None, 1)
check("p1 에 파일명 태그가 있으므로 검증 통과", "A10FV011" in got.tags, True)

print("\n" + "=" * 62)
print(f"  통과 {ok} / 실패 {fail}")
print("=" * 62)
sys.exit(1 if fail else 0)
