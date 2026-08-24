# -*- coding: utf-8 -*-
"""태그 규칙 자체 검증

    python fixtures/preprocess/test_tag.py

공식 규칙 (2026-08-24 이종수 책임 확인)

    Area - 설비종류 - 일련번호        A10-FV-001 · B10-PV-1631A
    └ 문자+숫자      └ FV·PV·LV…   └ 숫자(+접미)

Area 는 문자와 숫자를 합친 것이다(A10, B19). 문자는 공장이고 **없으면 A 가
생략된 것**이다 — 1공장이 최초 공장이라 지을 때 공장 구분이 필요 없었고,
2공장이 생기면서 B 를 붙이기 시작했다.

이 파일이 지키는 것
    ① `10-FV-012`(A구역)와 `B10-FV-012`(B구역)가 절대 같은 값이 되지 않는다
    ② 견적번호·스프링번호·클래스 표기를 태그로 잡지 않는다
    ③ `10-FV-011 / 012 / 013 / 014` 나열을 4개로 펼친다
    ④ 내보내는 값에는 문서에 없는 A 를 만들어 넣지 않는다
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.stdout.reconfigure(encoding="utf-8")

from src.preprocess import find_tags, normalize_tag, parse_filename, parse_tag

ok = fail = 0


def check(label, got, want):
    global ok, fail
    if got == want:
        ok += 1
        print(f"  OK   {label}")
    else:
        fail += 1
        print(f"  실패 {label}\n         받음 {got!r}\n         기대 {want!r}")


print("\n[1] 공장 문자 생략은 A 로 채운다")
for s in ["10-FV-012", "10FV012", "10 FV 012", "A10-FV-012", "A10FV012"]:
    check(f"{s:<12} → A10FV012", normalize_tag(s), "A10FV012")

print("\n[2] 구역이 다르면 다른 설비다 — 이 검사가 깨지면 마스터DB 가 오염된다")
check("10-FV-012 != B10-FV-012",
      normalize_tag("10-FV-012") != normalize_tag("B10-FV-012"), True)
check("B10-FV-012 → B10FV012", normalize_tag("B10-FV-012"), "B10FV012")
check("B10-PV-1631A → B10PV1631A", normalize_tag("B10-PV-1631A"), "B10PV1631A")

print("\n[3] 쪼갠 결과 — 공식 규칙의 Area 는 plant+unit")
t = parse_tag("B10-PV-1631A")
check("plant (공장)", t.plant, "B")
check("unit (호기)", t.unit, "10")
check("area = plant+unit", t.area, "B10")
check("kind (설비종류)", t.kind, "PV")
check("number (일련번호)", t.number, "1631")
check("suffix", t.suffix, "A")
check("raw 는 원문 그대로", t.raw, "B10-PV-1631A")
check("implicit_plant False", t.implicit_plant, False)

t2 = parse_tag("10-FV-012")
check("공장 문자 없으면 implicit_plant True", t2.implicit_plant, True)
check("그때 plant 는 A", t2.plant, "A")
check("area 는 A10", t2.area, "A10")
check("그래도 raw 에는 A 를 넣지 않는다", t2.raw, "10-FV-012")

print("\n[4] 태그가 아닌 것을 태그로 잡지 않는다")
for s in ["1E7924", "Quote No. 85-1874", "ANSI CLASS 600", "REV1", "REV. 2",
          '1 1/2" 300#', "N2 7kgf", "03-04 22:59 THU", "QUOT 11/1/85",
          "Serial No. J58278", "Form S-1017", "60.3.100x50"]:
    check(f"{s[:26]:<28} → 없음", normalize_tag(s), None)

print("\n[5] 나열 펼치기 — 다중 설비 사양표의 태그 대조가 이 형태다")
check("10-FV-011 / 012 / 013 / 014",
      find_tags("TAG NO. 10 - FV - 011 / 012 / 013 / 014"),
      ["A10FV011", "A10FV012", "A10FV013", "A10FV014"])
check("쉼표 나열",
      find_tags("10-FV-011, 012, 013, 014 Retrofit 4 Sets"),
      ["A10FV011", "A10FV012", "A10FV013", "A10FV014"])
check("접미만 바뀌는 나열", find_tags("B10-TV-481A / B"),
      ["B10TV481A", "B10TV481B"])
check("자리수가 다르면 나열이 아니다 (날짜)",
      find_tags("10-FV-011 / 2003"), ["A10FV011"])
check("숫자 표만 있는 줄", find_tags("Cv% / Signal% 8.2 / 17  28 / 44"), [])
check("유량 3열", find_tags("Flow Rate 42.0 / 123.0 / 200.0"), [])

print("\n[6] 파일명")
for name, key, raw, area in [
        ("10FV011-DATA SHEET_REV1.tif", "A10FV011", "10FV011", "A10"),
        ("B10FV1031-DATA SHEET_REV1.pdf", "B10FV1031", "B10FV1031", "B10"),
        ("10FV007B-DATA SHEET_REV0.tif", "A10FV007B", "10FV007B", "A10"),
]:
    i = parse_filename("raw_file/" + name)
    check(f"{name[:30]:<32} key={key}", (i.tag, i.tag_raw, i.area), (key, raw, area))

i = parse_filename("raw_file/070055_REV0.pdf")
check("070055_REV0.pdf 는 파일명에 태그 없음", i.tag, None)
check("문서 안에서는 찾는다 (B10-PV-1631A)",
      find_tags("Valve Tag # : B10-PV-1631A"), ["B10PV1631A"])

print("\n[7] B19 구역 예외 — 규칙을 풀어서 잡으려 하지 않는다")
# 2공장(B)은 미국에서 설비를 그대로 들여와 원 공장 태그를 쓴다. 자체 명명
# 규칙으로 바꾸면 기존 도면과 호환되지 않으므로 Area 표시만 앞에 붙였다.
# B19V10 은 B19 구역의 V10 설비다(문서 안 태그도 그냥 V10).
# 설비종류가 1자여서 규칙에 맞지 않는데, 1자를 허용하면 액추에이터 스프링
# 번호 1E7924 류가 대량으로 태그가 된다. 그래서 예외로 둔다 —
# 잃는 것은 파일명 태그 하나뿐이고 파일은 그대로 처리된다.
for name in ["B19V10-DATA SHEET_REV1.xlsx", "B19V1-DATA SHEET_REV0.xlsx"]:
    i = parse_filename("raw_file/" + name)
    check(f"{name[:30]:<32} 태그 없음", i.tag, None)
    check(f"{name[:30]:<32} 그래도 처리 대상", i.in_scope, True)
check("1자 설비종류를 허용하지 않는다", normalize_tag("1E7924"), None)

print("\n[8] 내보내는 값에는 A 를 만들어 넣지 않는다 (철학 4)")
i = parse_filename("raw_file/10FV011-DATA SHEET_REV1.tif")
check("정규화 키에는 A 가 있고", i.tag, "A10FV011")
check("표시용 원문에는 없다", i.tag_raw, "10FV011")
check("규칙으로 채웠다는 표시가 남는다", i.tag_parts.implicit_plant, True)

print("\n" + "=" * 62)
print(f"  통과 {ok} / 실패 {fail}")
print("=" * 62)
sys.exit(1 if fail else 0)
