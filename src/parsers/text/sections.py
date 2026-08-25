# -*- coding: utf-8 -*-
"""③-a TEXT PARSER — 구역(section) 인식.

■ 무슨 작업인가
────────────────────────────────────────────────────────────────────
데이터시트는 밸브 한 대의 사양을 **부품별로 묶어** 적는다. 그래서 같은 항목명이
부품마다 되풀이된다. 실물 `52PV014` 한 장에 이렇게 들어 있다.

    21  Model              2121            ← 밸브 본체 모델   (우리가 찾는 값)
    34  Material           WCB             ← 밸브 본체 재질
    40  Stem Material      316 SST         ← 트림 재질
    47  Model No.          880             ← 액추에이터 모델  (우리 필드 아님)
    56  Model No. / Mfr.   I/P5000, SMC    ← 포지셔너 모델

항목명만 보면 어느 것이 표준 필드 `MODEL NO.`(밸브 본체 모델)인지 알 수 없다.
실제로 파서는 `Model No.`(액추에이터 880)를 밸브 모델로 집어 오답을 냈다.
`Maker` 도 같은 이유로 유사표현에서 빼야 했고, 그 결과 MVP 9필드 중
`MANUFACTURER` 만 유사표현이 0개로 남아 있었다.

■ 문서는 이미 답을 갖고 있다
────────────────────────────────────────────────────────────────────
묶음마다 이름표가 붙어 있다. 형식에 따라 저장 방식만 다르다.

    PDF   여백에 90도 회전된 글자    dir=(0,-1) 인 span
          예) "VALVE BODY / BONNET"  x=57~66  y=329~417

    엑셀  세로로 병합된 셀            행 범위가 아예 명시돼 있다
          예) "VALVE BODY / BONNET"  행 24~37  열 1~4

이 모듈은 그 이름표를 찾아 **각 라벨이 어느 구역에 있는지**를 돌려준다.

■ 설계 결정 두 가지
────────────────────────────────────────────────────────────────────
① 구역은 이름을 만드는 장치가 아니라 **거르는 장치**다.
   결합 키("VALVE BODY / BONNET Model")로 사전에 넣지 않는다. 구역 표기
   변종(`VALVE BODY / BONNET` · `BODY` · `Valve Body Assembly`)만큼 사전이
   배로 늘기 때문이다. 유사표현은 이름만 등록하고, 한 이름을 여러 필드에
   등록해 두고서 구역으로 고른다 (`FieldIndex.lookup(label, allowed=...)`).

② **사전에 등록된 이름만 구역으로 인정**한다 (`schema/rules.yaml` 의 `sections`).
   10PV018 정비보고서에는 `Authorized by` 같은 세로 병합 셀이 5개 있다.
   모양만 보고 구역으로 삼으면 그런 것까지 구역이 된다.

■ 한계 — 구역이 없으면 아무것도 하지 않는다
────────────────────────────────────────────────────────────────────
채점 대상 9건 중 6건에만 구역 구조가 있다. 없는 문서(`11FV048` 리트로핏
점검서 등)는 `None` 을 돌려주고, 파서는 지금까지와 똑같이 동작한다.
구역 인식은 **더 알 때만 더 하는** 장치이지, 못 하면 막는 장치가 아니다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import yaml

from .field_index import normalize_label

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
RULES_PATH = os.path.join(ROOT, "schema", "rules.yaml")

# ── PDF 쪽 상수 ───────────────────────────────────────────────────
# 회전 텍스트라도 한두 글자짜리는 구역 이름이 아니다. 실물에서 'S' · 'g' 같은
# 조각이 회전으로 잡힌다 (표 테두리에 걸친 글자).
MIN_MARK_LEN = 3

# 같은 여백 열에 선 이름표끼리 묶는다. 실물은 x=57~66 / x=295~303 처럼
# 10pt 안쪽에 모여 서므로 넉넉히 잡아도 두 밴드가 섞이지 않는다.
BAND_TOL = 24.0


@dataclass(frozen=True)
class Mark:
    """구역 이름표 하나.

    text   문서에 적힌 그대로 (`VALVE BODY / BONNET`)
    key    표준 구역 (`body`) — 사전에 없으면 이 객체를 만들지 않는다
    lo/hi  지배 범위. 엑셀은 행 번호, PDF 는 y 좌표
    col    이름표가 선 위치. 엑셀은 열 번호, PDF 는 x 좌표.
           **자기 오른쪽에 있는 라벨만** 지배한다 (2단 양식에서 왼쪽 단과
           오른쪽 단이 각자의 이름표를 갖는다)
    """
    text: str
    key: str
    lo: float
    hi: float
    col: float
    anchor: tuple[int, int] | None = None   # 엑셀에서 이름표가 적힌 칸 (행, 열)


class SectionIndex:
    """`schema/rules.yaml` 의 구역 사전.

    - `standard("VALVE BODY / BONNET")` → `"body"`
    - `allowed("body")` → 그 구역에서 나올 수 있는 field key 집합
    """

    def __init__(self, aliases: dict, fields: dict):
        # 표기 흔들림 흡수 — 라벨과 같은 정규화를 쓴다.
        # "ACT'N" · "ACC. OTHERS" 처럼 구두점이 섞인 표기가 많다.
        self._alias = {normalize_label(k): v for k, v in (aliases or {}).items()}
        self._fields = {k: set(v or []) for k, v in (fields or {}).items()}

    @classmethod
    def load(cls, path: str = RULES_PATH) -> "SectionIndex":
        if not os.path.exists(path):
            return cls({}, {})
        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
        sec = doc.get("sections") or {}
        return cls(sec.get("aliases"), sec.get("fields"))

    def standard(self, text: object) -> str | None:
        """문서 표기 → 표준 구역. 사전에 없으면 None (구역으로 보지 않는다)."""
        return self._alias.get(normalize_label(text))

    def allowed(self, key: str | None) -> set[str] | None:
        """표준 구역에서 허용되는 field key.

        None 을 돌려주면 "구역을 모른다" 는 뜻이고, 호출부는 지금까지처럼
        이름만으로 매핑한다. 빈 집합은 "이 구역에서는 아무 필드도 안 나온다"
        는 뜻이라 뜻이 완전히 다르다 (LIMIT SW · ACCESSORIES 등).
        """
        if key is None:
            return None
        return self._fields.get(key)

    def __len__(self) -> int:
        return len(self._alias)


class SectionMap:
    """이름표 목록 + "이 위치는 어느 구역인가" 조회.

    엑셀과 PDF 가 같은 구조(lo~hi 범위 + 왼쪽 경계)로 환원되므로 한 클래스로
    쓴다. 만드는 방법만 다르다 — `from_excel()` · `from_pdf()`.
    """

    def __init__(self, marks: list[Mark]):
        self.marks = marks

    def __bool__(self) -> bool:
        return bool(self.marks)

    def at(self, pos: float, col: float) -> str | None:
        """(범위 좌표, 열/ x 좌표) 위치의 표준 구역. 모르면 None.

        규칙은 하나다 — **자기 왼쪽에 있는 이름표 중 가장 가까운 것**.
        2단 양식에서 왼쪽 단 라벨(x≈90)은 x≈57 이름표에, 오른쪽 단
        라벨(x≈327)은 x≈295 이름표에 붙는다.
        """
        cands = [m for m in self.marks if m.col < col and m.lo <= pos <= m.hi]
        if not cands:
            return None
        return max(cands, key=lambda m: m.col).key

    def is_marker(self, row: int, col: int) -> bool:
        """이 칸이 구역 이름표 자신인가.

        이름표는 라벨이 아니다. `MATERIAL` 같은 이름표를 라벨로 읽으면 옆 칸을
        값으로 집어 엉뚱한 값을 만든다 (실물 44LV001 배치에서 확인).
        """
        return any(m.anchor == (row, col) for m in self.marks)

    # ── 엑셀 ──────────────────────────────────────────────────────
    @classmethod
    def from_excel(cls, ws, index: SectionIndex) -> "SectionMap":
        """세로로 병합된 셀에서 이름표를 찾는다.

        엑셀은 행 범위가 병합 정보에 그대로 있어 PDF 보다 정확하다.
        가로로만 병합된 칸(제목·머리글)은 구역이 아니므로 뺀다.
        """
        marks: list[Mark] = []
        for rng in ws.merged_cells.ranges:
            if rng.max_row - rng.min_row < 1:        # 한 행짜리는 구역이 아니다
                continue
            text = ws.cell(rng.min_row, rng.min_col).value
            key = index.standard(text)
            if key is None:
                continue
            marks.append(Mark(str(text).strip(), key,
                              rng.min_row, rng.max_row, rng.max_col,
                              anchor=(rng.min_row, rng.min_col)))
        return cls(marks)

    # ── PDF ───────────────────────────────────────────────────────
    @classmethod
    def from_pdf(cls, page, index: SectionIndex) -> "SectionMap":
        """90도 회전된 텍스트에서 이름표를 찾는다.

        ⚠️ 회전 이름표는 블록의 **세로 중앙**에 온다. 위쪽이 아니다.
           그래서 "이 이름표부터 다음 이름표까지" 로 자르면 한 블록씩 밀린다.
           같은 여백 열에 선 이름표들을 y 순으로 세우고 **이웃한 중심의
           중점**을 경계로 삼는다.
        """
        raw: list[tuple[str, str, float, float, float]] = []   # text,key,x,y0,y1
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                if line.get("dir", (1.0, 0.0)) == (1.0, 0.0):
                    continue                          # 가로 글자는 구역이 아니다
                for span in line["spans"]:
                    text = span["text"].strip()
                    if len(text) < MIN_MARK_LEN:
                        continue
                    key = index.standard(text)
                    if key is None:
                        continue
                    x0, y0, _, y1 = span["bbox"]
                    raw.append((text, key, x0, y0, y1))

        # 같은 여백 열끼리 묶는다 (왼쪽 단 이름표 / 오른쪽 단 이름표)
        bands: dict[float, list] = {}
        for item in sorted(raw, key=lambda r: r[2]):
            for x in bands:
                if abs(item[2] - x) <= BAND_TOL:
                    bands[x].append(item)
                    break
            else:
                bands[item[2]] = [item]

        marks: list[Mark] = []
        for x, items in bands.items():
            items.sort(key=lambda r: (r[3] + r[4]) / 2)        # 중심 y 순
            centers = [(r[3] + r[4]) / 2 for r in items]
            for i, (text, key, x0, y0, y1) in enumerate(items):
                # 안쪽 경계는 이웃 중심과의 중점.
                up = (centers[i - 1] + centers[i]) / 2 if i > 0 else None
                dn = (centers[i] + centers[i + 1]) / 2 if i < len(items) - 1 else None

                # 바깥쪽(맨 위·맨 아래)은 무한대로 늘리지 않는다. 표 밖의 문서
                # 머리글까지 첫 구역에 딸려 들어가기 때문이다 — 실물 19FV077 의
                # `Valve Tag # : 19-FV-077`(y=29)이 맨 위 `Actuator`(y=114~) 구역으로
                # 잡혀 태그가 통째로 버려졌다. 안쪽 경계까지의 거리를 그대로
                # 반대편에 접어 쓰고, 이웃이 없으면 자기 키를 쓴다.
                own = max(y1 - y0, 1.0)
                lo = up if up is not None else centers[i] - ((dn - centers[i]) if dn else own)
                hi = dn if dn is not None else centers[i] + ((centers[i] - up) if up else own)
                marks.append(Mark(text, key, lo, hi, x0))
        return cls(marks)
