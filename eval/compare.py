# -*- coding: utf-8 -*-
"""값 대조 규칙 — 골든셋 정답과 추출값이 같은가

    from eval.compare import same, why

    same("142.6 m3/Hr", "142.6 M3/H")   → True   (단위 표기 무시)
    same("CLASS 4",     "CLASS IV")     → True   (로마자 정규화)
    same("FAIL OPEN",   "FAIL CLOSE")   → False
    same("A351 CF8M",   "A351 CF3M")    → False  (재질 등급은 숫자로 뭉개지 않는다)

무엇을 감점하고 무엇을 감점하지 않는가 — 결정 근거
─────────────────────────────────────────────────────────────
① **단위 표기 변형은 감점하지 않는다** (2026-08-24)
   옛 데이터시트는 단위를 사람이 손으로 썼다. `H` · `Hr` · `hr` 이 전부
   Hour 이고 `m3/H` · `M3/Hr` · `m3h` 가 전부 같은 단위다. 표기를 틀렸다고
   깎으면 측정하려는 것(값을 제대로 읽었나)이 아니라 필사 습관을 재게 된다.
   → 숫자가 나오는 필드는 **숫자만** 대조한다.

   단, **"숫자 + 단위" 형태일 때만** 그렇게 한다. 단위 어휘를 걷어낸 뒤 남는
   것이 숫자·구두점뿐이면 값으로 보고 숫자만 대조하고, 글자가 남으면 텍스트로
   대조한다. `142.6 m3/Hr` 은 숫자 대조로 가지만 `A351 CF8M` 은 텍스트 대조로
   간다 — 재질 등급을 숫자로 뭉개면 `CF8M` 과 `CF3M` 이 같아진다.

② **로마자와 아라비아 숫자는 같다** (2026-08-24)
   누설등급이 `CLASS 4` · `CLASS IV` · `Class IV` · `ANSI Class II` 로 섞여
   나온다. 골든셋 8건에서 이미 세 표기가 관측되었다. 로마자를 아라비아로 바꿔
   비교하므로 `CLASS 4` == `CLASS IV` 이면서 `CLASS 4` != `CLASS 2` 다.
   실측상 로마자가 나오는 필드는 누설등급이 거의 유일하다.

③ **대소문자·공백·구두점은 감점하지 않는다**
   `metso` vs `METSO`, `Fail Close` vs `FAIL CLOSE`.

④ **N/A 는 값이 아니다**
   문서에 근거가 없다는 뜻이므로 `NA` 끼리만 맞는다. `NA` 와 빈칸도 같게 본다.
   `판독불가` 는 별도 상태로 구분한다 — 항목은 있는데 못 읽은 것이다.

⑤ **확신 표시(`?`)는 떼고 비교한다**
   라벨러가 `?316SST` 처럼 물음표를 붙인다. 값 자체는 그대로 쓰고,
   집계에서 "라벨러 불확실" 로 따로 센다.

⑥ **텍스트 필드는 별칭 사전에 맡긴다**
   `EQ%` vs `Equal Percent`, `Diaph.` vs `DIAPHRAGM` 은 표기 변종이지
   대조 규칙으로 풀 문제가 아니다. `schema/rules.yaml` 의 `value_aliases` 를
   통과시킨 뒤 이 함수를 부른다. 여기서 억지로 맞추면 사전이 자라지 않는다.
"""
from __future__ import annotations

import re
import unicodedata

NA_TOKENS = {"", "NA", "N/A", "N.A.", "NONE", "-", "—", "없음"}
UNREADABLE_TOKENS = {"판독불가", "ILLEGIBLE", "UNREADABLE"}

# 로마자 → 아라비아. 누설등급은 I~X 를 넘지 않는다.
_ROMAN = [("VIII", 8), ("VII", 7), ("VI", 6), ("IX", 9), ("IV", 4),
          ("XII", 12), ("XI", 11), ("X", 10), ("V", 5),
          ("III", 3), ("II", 2), ("I", 1)]
_ROMAN_RE = re.compile(r"\b(X{0,2}(?:IX|IV|V?I{0,3}))\b")
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")

# 단위 어휘 — 이것만 걷어낸 뒤 숫자·구두점만 남으면 "값" 으로 본다.
#
# 왜 목록이 필요한가: `m3/Hr` 의 `3`, `kg/cm2` 의 `2` 를 값으로 세면
# `142.6 m3/Hr` 이 [142.6, 3] 이 되어 단위가 다른 문서와 비교가 깨진다.
# 반대로 모든 글자를 무시하면 `A351 CF8M` 과 `A351 CF3M` 이 같아진다.
#
# ⚠️ 여기에 늘려 쓰지 말 것. 표기 변종(`ANSI CLASS 300` vs `300#`)은 단위가
#    아니라 별칭이고 `schema/rules.yaml` 의 value_aliases 가 처리한다.
#    이 목록도 최종적으로는 rules.yaml 로 옮기는 것이 맞다(철학 2).
UNITS = {
    "m3", "m³", "cm2", "cm²", "mm2", "kg", "kgf", "lb", "ton",
    "h", "hr", "hrs", "hour", "min", "sec", "s", "d",
    "in", "inch", "mm", "cm", "m", "ft",
    "c", "f", "k", "℃", "℉", "°c", "°f", "degc", "degf",
    "cp", "cst", "dba", "psi", "psig", "bar", "barg", "mpa", "kpa",
    "l", "gal", "ma", "v", "sg", "g", "gr", "spgr", "pa",
}
# 문자열을 토큰으로 쪼갠다.
# 글자로 시작하면 뒤에 붙은 숫자까지 한 덩어리로 읽는다 — `m3` 를 `m`+`3` 으로
# 쪼개면 `m`(미터)만 단위로 걸러지고 `3` 이 값으로 남는다.
# 숫자로 시작하는 것은 값이므로 따로 둔다 (`316SST` → `316` + `SST`).
_TOKEN_RE = re.compile(r"[A-Za-z°℃℉]+\d*|\d+(?:\.\d+)?|.")


def _strip_units(s: str) -> str:
    """단위로 알려진 글자 덩어리를 지운다. 나머지는 그대로 둔다."""
    out = []
    for t in _TOKEN_RE.findall(s):
        if t[0].isalpha() or t[0] in "°℃℉":
            if t.lower() in UNITS:
                continue
        out.append(t)
    return "".join(out)


def looks_numeric(s) -> bool:
    """`숫자 + 단위` 형태인가. 단위를 걷어내면 숫자·구두점만 남는가."""
    t = _strip_units(roman_to_arabic(_clean(s)))
    return bool(_NUM_RE.search(t)) and not re.search(r"[A-Za-z가-힣]", t)


def _clean(s) -> str:
    if s is None:
        return ""
    t = unicodedata.normalize("NFKC", str(s)).strip()
    if t.startswith("?"):            # 라벨러 확신 표시
        t = t[1:].strip()
    return t


def is_na(s) -> bool:
    return _clean(s).upper() in NA_TOKENS


def is_unreadable(s) -> bool:
    return _clean(s).upper() in {t.upper() for t in UNREADABLE_TOKENS}


def uncertain(s) -> bool:
    """라벨러가 확신하지 못한 값인가 (`?316SST`)."""
    return str(s or "").strip().startswith("?")


def roman_to_arabic(s: str) -> str:
    """문자열 안의 로마자를 아라비아 숫자로 바꾼다. 로마자가 없으면 그대로."""
    def sub(m):
        r = m.group(1)
        if not r:
            return m.group(0)
        total, i = 0, 0
        while i < len(r):
            for sym, val in _ROMAN:
                if r.startswith(sym, i):
                    total += val
                    i += len(sym)
                    break
            else:
                return m.group(0)        # 로마자가 아니다
        return str(total)
    return _ROMAN_RE.sub(sub, s.upper())


def numbers(s) -> list[float]:
    """문자열에서 값 숫자를 순서대로 뽑는다. 단위 안의 숫자는 세지 않는다.

    숫자 사이의 하이픈은 구분자로 본다 — `1-1/2"`(1과 1/2 인치)의 `-` 를
    음수로 읽으면 `1 1/2 in` 과 달라진다. 음수는 앞이 공백·시작일 때만.
    """
    t = _strip_units(roman_to_arabic(_clean(s)))
    t = re.sub(r"(?<=\d)\s*-\s*(?=\d)", " ", t)
    return [float(x) for x in _NUM_RE.findall(t)]


def norm_text(s) -> str:
    """대소문자·공백·구두점을 지운 비교용 문자열."""
    t = roman_to_arabic(_clean(s))
    return re.sub(r"[^A-Z0-9가-힣]+", "", t)


def same(gold, got) -> bool:
    """골든셋 정답과 추출값이 같은가."""
    if is_na(gold) or is_na(got):
        return is_na(gold) and is_na(got)
    if is_unreadable(gold) or is_unreadable(got):
        return is_unreadable(gold) and is_unreadable(got)

    # 양쪽 모두 "숫자 + 단위" 일 때만 숫자로 대조한다.
    # 한쪽이라도 글자가 남으면 텍스트로 — 재질 등급을 숫자로 뭉개지 않기 위해.
    if looks_numeric(gold) and looks_numeric(got):
        return numbers(gold) == numbers(got)
    return norm_text(gold) == norm_text(got)


def why(gold, got) -> str:
    """같지 않을 때 사유. 같으면 빈 문자열. 로그·화면에 쓴다."""
    if same(gold, got):
        return ""
    if is_na(gold) and not is_na(got):
        return f"정답은 근거 없음(N/A)인데 값을 만들었다: {_clean(got)!r}"
    if is_na(got) and not is_na(gold):
        return f"정답 {_clean(gold)!r} 을 찾지 못하고 N/A 로 두었다"
    if is_unreadable(gold):
        return "정답이 판독불가 — 사람도 못 읽은 값이다"
    if looks_numeric(gold) and looks_numeric(got):
        return f"숫자가 다르다: 정답 {numbers(gold)} vs 추출 {numbers(got)}"
    return f"값이 다르다: 정답 {_clean(gold)!r} vs 추출 {_clean(got)!r}"
