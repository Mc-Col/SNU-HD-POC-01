# -*- coding: utf-8 -*-
"""
전처리 유틸리티 — 파일명 파싱 · 페이지 렌더링 · 텍스트 레이어 탐침

강민호 책임의 src/triage · src/router 가 이 함수들을 조립해서 쓴다.
여기에는 판정 로직을 넣지 않는다 — 사실을 수집하는 도구만 둔다.

    from src import preprocess as pre

    info  = pre.parse_filename(path)        # 태그 · 문서종류 · rev
    pages = pre.probe_pages(path)           # 페이지별 텍스트 레이어 유무
    pngs  = pre.render_pages(path, out_dir) # 페이지 → PNG
    grid  = pre.make_montage(pngs, out)     # 격자 (VLM 이진 판정용)

    # 사양표가 2장 이상일 때 — 최신 한 장을 고른다
    d     = pre.parse_doc_date(txt)         # 페이지에 적힌 날짜
    mark  = pre.find_marks(txt)             # RETROFIT / OLD 표기
    pick, why = pre.pick_latest_spec(page_infos, file_tag)

    # 태그별 형제 문서 — 데이터시트가 낡았을 수 있다는 경고
    idx  = pre.index_by_tag(all_paths)
    warn = pre.staleness_warning(path, idx)

실측 근거 (raw_file 1,058건, 2026-08-24):
    파일명에서 문서종류 판정      97.3%   ← 파일 단위 판정이 거의 무료
    파일명에서 태그 추출          99.2%
    PDF 페이지 중 텍스트 보유     68.1%
    PDF 파일 중 텍스트/스캔 혼재  57.4%   ← 그래서 페이지 단위로 판정해야 한다
    다중 페이지 파일              85%     (최대 61페이지)
"""
from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable

# 페이지당 텍스트가 이 글자 수 이상이면 텍스트 레이어가 있는 것으로 본다.
# 실측 결과 20~500자 사이에서 판정이 거의 변하지 않는다 — 텍스트 페이지는
# 중앙값 2,055자이고 스캔은 0자대여서 그 사이가 비어 있다. 튜닝 불필요.
TEXT_LAYER_MIN = 100

EXCEL_EXT = {".xlsx", ".xlsm", ".xls"}
IMAGE_EXT = {".tif", ".tiff", ".jpg", ".jpeg", ".png"}
PDF_EXT = {".pdf"}
SUPPORTED = EXCEL_EXT | IMAGE_EXT | PDF_EXT

# 손글씨 스캔의 판독을 위해 기본 해상도를 넉넉히 둔다.
# 원본이 150 DPI 1비트라 그대로 쓰면 손글씨가 뭉개진다.
RENDER_DPI = 200


# ══════════════════════════════════════════════════════════════
#  파일명 파싱
# ══════════════════════════════════════════════════════════════

# 문서 종류 — 오타와 뒤 공백이 실제로 존재한다(REPIAR REPORT, "REPAIR REPORT ").
# 느슨하게 잡되 긴 패턴을 먼저 검사한다.
# 오타·변형이 실제로 존재한다 — REPIAR REPORT, DATA SHEEET, INSTURMENT,
# 뒤 공백("REPAIR REPORT "). 느슨하게 잡되 **긴 패턴을 먼저** 검사한다
# (INSTRUMENT LIST 가 INSTRUMENT 보다 앞이어야 목록이 대상으로 새지 않는다).
DOC_KINDS: list[tuple[str, str, bool]] = [
    # (표시명, 정규식, 대상 여부)
    ("SPECIFICATION DATA SHEET", r"SPEC\w*\s*DATA\s*SHE+T",  True),
    ("DATA SHEET",               r"DATA\s*SHE+T",            True),   # SHEEET 오타 포함
    ("SPEC & CALC",              r"SPEC\s*&\s*CALC",         True),
    ("REPAIR REPORT",            r"REP[AI]{2}R\s*REPORT",    False),
    ("RETROFIT REPORT",          r"RETROFIT\s*REPORT",       False),
    ("RETROFIT",                 r"RETROFIT",                False),
    ("TEST REPORT",              r"TEST\s*REPORT",           False),
    ("INSTRUMENT LIST",          r"INST[UR]{2}MENT\s*LIST",  False),  # 목록은 대상 아님
    ("DRAWING",                  r"DRAWING",                 False),
    ("SPECIFICATION",            r"SPECIFICATION",           True),
]

# 파일명으로 못 가리는 것들 — 실물 확인 결과 (2026-08-24)
#   30FV522C-DATA-001_REV0.pdf   내용은 "Valve Data Sheet" → 대상. 이름에 SHEET 없음
#   070055_REV0.pdf              내용은 "Control Valve Specification".
#                                태그가 파일명이 아니라 문서 안에 있다(B10-PV-1631A)
#   20LV009-INSTRUMENT-001       내용은 RADIOGRAPHIC EXAMINATION REPORT → 제외 대상
#   30PV003_REV0.tif             9페이지 스캔, 태그만 있는 이름
# 이름을 더 짜맞추지 않는다 — 내용 판정(격자 → VLM)으로 보낸다. 2.5% 다.

# ── 태그 규칙 ────────────────────────────────────────────────────
#
#  공식 명명 규칙 (2026-08-24 이종수 책임 확인)
#
#      Area - 설비종류 - 일련번호        A10-FV-001 · B10-PV-1631A
#      └ 문자+숫자      └ FV·PV·LV…   └ 숫자(+접미 A/B)
#
#  Area 는 **문자와 숫자를 합친 것**이다(A10, B19). 문자는 공장이고,
#  **없으면 A 가 생략된 것이다** — 1공장이 최초 공장이라 지을 때는 공장
#  구분이 필요 없었고, 2공장이 생기면서 B 를 붙이기 시작했다. 즉
#  `10-FV-012` 의 정식 표기는 `A10-FV-012` 다.
#
#  그래서 비교용 정규화 키에는 공장 문자를 **항상** 넣는다. 넣지 않으면
#  `10-FV-012`(A구역)와 `B10-FV-012`(B구역)가 같은 값이 되어 서로 다른
#  설비가 충돌한다. 현재 코퍼스에서는 충돌 0건이지만 30만 태그에서는 터진다.
#
#  ⚠️ 화면·엑셀에 내보내는 값은 문서에 적힌 그대로 쓴다(`tag_raw`).
#     정규화 키는 대조에만 쓴다 — 문서에 없는 A 를 값으로 만들지 않는다(철학 4).
#
#  설비종류는 2~4자만 인정한다(FV·PV·LV·PCV·PDV…). 1자를 허용하면
#  액추에이터 스프링 번호 `1E7924` 같은 것이 태그로 잡힌다.
#
#  ── 알려진 예외: B19 구역 ────────────────────────────────────────
#  2공장(B)은 미국에서 설비를 그대로 들여왔다. 그래서 자체 명명 규칙이
#  아니라 원래 공장의 태그를 그대로 쓴다. 도면 호환 때문에 이름을 바꿀 수
#  없어 Area 표시만 앞에 붙였다 — `B19V10` 은 B19 구역의 `V10` 설비다
#  (문서 안 태그도 그냥 `V10`).
#
#  실측 2건(`B19V1`, `B19V10`)뿐이고 사용자가 예외로 빼도 된다고 확인했다.
#  규칙을 느슨하게 풀어 이것을 잡으려 하지 않는다 — 1자 설비종류를 허용하는
#  순간 `1E7974` 류가 대량으로 태그가 된다. 잃는 것은 **파일명 태그 하나**
#  뿐이고 파일은 그대로 처리된다(`in_scope=True`). 태그는 문서에서 읽는다.

DEFAULT_PLANT = "A"

TAG_RE = re.compile(
    r"\b([A-Z])?\s*-?\s*(\d{1,3})\s*-?\s*([A-Z]{2,4})\s*-?\s*(\d{2,4})\s*-?\s*([A-Z]{0,2})\b")
REV_RE = re.compile(r"_?REV\.?\s*(\d+)", re.I)


@dataclass
class TagParts:
    """태그를 규칙대로 쪼갠 것. `key` 가 비교용 정규화 값이다.

    공식 규칙의 `Area` 는 `plant + unit` 이다(B + 19 = B19). 대조할 때
    공장만 또는 호기만 비교하는 일이 있어 따로 들고 있다.
    """
    plant: str = DEFAULT_PLANT      # 공장 — A(1공장) / B(2공장)
    unit: str = ""                  # 호기 — 10, 19 …
    kind: str = ""                  # 설비종류 — FV, PV, PCV …
    number: str = ""                # 일련번호
    suffix: str = ""                # 접미 — A, B …
    raw: str = ""                   # 원문에 적힌 그대로
    implicit_plant: bool = False    # 원문에 공장 문자가 없어 A 로 채웠는가

    @property
    def area(self) -> str:
        """공식 규칙의 Area (A10, B19)."""
        return f"{self.plant}{self.unit}"

    @property
    def key(self) -> str:
        return f"{self.area}{self.kind}{self.number}{self.suffix}"

    def __bool__(self) -> bool:
        return bool(self.unit and self.kind and self.number)


@dataclass
class FileNameInfo:
    path: str
    stem: str
    ext: str
    tag: str | None = None          # 정규화 키 (A10FV012) — 대조용
    tag_raw: str | None = None      # 파일명에 있던 그대로 (10FV012) — 표시용
    tag_parts: TagParts | None = None
    doc_kind: str = ""              # 표시명
    in_scope: bool | None = None    # True 대상 / False 제외 / None 판단 불가
    rev: str = ""

    @property
    def supported_ext(self) -> bool:
        return self.ext in SUPPORTED

    @property
    def area(self) -> str:
        """공식 규칙의 Area (A10, B19). 태그가 없으면 빈 문자열."""
        return self.tag_parts.area if self.tag_parts else ""

    @property
    def plant(self) -> str:
        return self.tag_parts.plant if self.tag_parts else ""


def parse_tag(s: str | None) -> TagParts | None:
    """문자열에서 태그 하나를 규칙대로 쪼갠다. 못 찾으면 None."""
    if not s:
        return None
    m = TAG_RE.search(unicodedata.normalize("NFKC", str(s)).upper())
    if not m:
        return None
    plant, unit, kind, num, suf = m.groups()
    return TagParts(plant=plant or DEFAULT_PLANT, unit=str(int(unit)), kind=kind,
                    number=num, suffix=suf or "", raw=m.group(0).strip(),
                    implicit_plant=not plant)


def normalize_tag(s: str | None) -> str | None:
    """비교용 정규화 키. Area 를 항상 포함한다.

        10-FV-012 · 10FV012 · 10 FV 012 · A10-FV-012  →  A10FV012
        B10-PV-1631A                                  →  B10PV1631A

    파일명 태그와 문서 안 태그를 대조하려면 이 정규화가 필수다.
    내보내는 값에는 쓰지 않는다 — `TagParts.raw` 를 쓴다.
    """
    t = parse_tag(s)
    return t.key if t else None


# 태그를 나열할 때 뒤쪽은 번호만 적는다 — `10-FV-011 / 012 / 013 / 014`.
# 다중 설비 사양표의 태그 대조가 정확히 이 형태이므로 반드시 펼쳐야 한다.
# 자리수가 같은 번호만 인정한다 — `10-FV-011 / 2003`(날짜)을 태그로 잡지 않기 위해.
_RUN_NUM = re.compile(r"\s*[/,]\s*(\d{2,4})\s*([A-Z]{0,2})(?![A-Z0-9])")
_RUN_SUF = re.compile(r"\s*[/,]\s*([A-Z]{1,2})(?![A-Z0-9])")


def find_tags(text: str) -> list[str]:
    """텍스트에서 태그를 모두 뽑는다(정규화 키). 순서 유지, 중복 제거.

    나열 표기를 펼친다:
        10-FV-011 / 012 / 013 / 014  →  A10FV011 A10FV012 A10FV013 A10FV014
        B10-TV-481A / B              →  B10TV481A B10TV481B
    """
    up = unicodedata.normalize("NFKC", str(text)).upper()
    out, seen = [], set()

    def add(t: str) -> None:
        if t not in seen:
            seen.add(t); out.append(t)

    pos = 0
    for m in TAG_RE.finditer(up):
        if m.start() < pos:          # 나열로 이미 소비한 구간
            continue
        plant, unit, kind, num, suf = m.groups()
        area = f"{plant or DEFAULT_PLANT}{int(unit)}"
        add(f"{area}{kind}{num}{suf or ''}")

        # 같은 Area·설비종류를 공유하는 나열을 이어서 읽는다
        pos = m.end()
        while True:
            r = _RUN_NUM.match(up, pos)
            if r and len(r.group(1)) == len(num):
                add(f"{area}{kind}{r.group(1)}{r.group(2)}")
                pos = r.end(); continue
            r = _RUN_SUF.match(up, pos)
            if r and suf:            # 접미만 바뀌는 나열 (481A / B)
                add(f"{area}{kind}{num}{r.group(1)}")
                pos = r.end(); continue
            break
    return out


def parse_filename(path: str) -> FileNameInfo:
    """파일명에서 태그·문서종류·rev 를 뽑는다. 문서종류 97.3% 가 여기서 해결된다."""
    base = os.path.basename(path)
    stem, ext = os.path.splitext(base)
    info = FileNameInfo(path=path, stem=stem, ext=ext.lower())

    up = unicodedata.normalize("NFKC", stem).upper()

    for name, pat, scope in DOC_KINDS:
        if re.search(pat, up):
            info.doc_kind, info.in_scope = name, scope
            break

    # 태그는 파일명 앞부분에서 찾는다 (10FV012-DATA SHEET_REV1).
    # 앞부분에 없으면 전체에서 찾되, 문서종류 단어에 걸린 것은 버린다.
    head = re.split(r"[-_]", stem, maxsplit=1)[0]
    parts = parse_tag(head) or parse_tag(stem)
    if parts:
        info.tag_parts = parts
        info.tag = parts.key
        info.tag_raw = parts.raw

    m = REV_RE.search(stem)
    if m:
        info.rev = f"REV{m.group(1)}"
    return info


# ══════════════════════════════════════════════════════════════
#  페이지 탐침 — 페이지별 텍스트 레이어 유무
# ══════════════════════════════════════════════════════════════

@dataclass
class PageText:
    page: int                       # 1부터
    text: str = ""
    has_text_layer: bool = False
    width: int = 0
    height: int = 0
    locator: str = ""               # 엑셀 시트명 등

    @property
    def text_len(self) -> int:
        return len(self.text.strip())


# ══════════════════════════════════════════════════════════════
#  정비·개조 보고서 — 추출 대상이 아닌 이유
# ══════════════════════════════════════════════════════════════
#
#  2026-08-24 범위 결정 (사용자). 112건(정엔지니어링 46 · Valstone 49 · 기타 17)
#  을 추출 대상에서 제외한다. **글씨를 못 읽어서가 아니라 사양이 안 적혀 있어서다.**
#
#  `CONTROL VALVE REPAIR & RETROFIT CHECK SHEET` 의 SPECIFICATIONS 칸은
#  사양서가 아니라 작업 기록이다. 실측 2건:
#      15FV031   사양 22칸 중 12칸 기재. RATED CV·MODEL NO. 공란
#      10PCV072  사양 22칸 중  2칸 기재(Rating, Maker). 나머지 전부 공란
#  텍스트 레이어에 `Rated Cv` 라벨이 있는 건 46건 중 32.6%, 값까지 붙어
#  나오는 건 4.3% 다.
#
#  두 번째 이유 — 시점이 섞인다. 15FV031 을 제대로 채우려면
#      MODEL NO. 667-A · RATED CV 34.1  ← p8 Fisher 원본(1986, 사진)
#      POSITIONER DVC 6200              ← p7 체크시트(2022, 수기)
#  즉 한 페이지를 고르는 문제가 아니라 **시점이 다른 페이지를 합치는 문제**다.
#  정비보고서를 빼면 "파일 안에서 페이지 하나를 고른다"는 규칙이 유지된다
#  (10FV011 의 2003 Retrofit 사양서는 혼자서 MVP 9필드가 전부 채워진다).
#
#  제외는 구멍이 아니라 측정 항목으로 둔다 — 골든셋에 정비보고서를
#  "제외가 정답" 으로 넣어 Triage 가 추출을 시도하지 않는지 채점한다.

REPORT_DOC_KINDS = ("RETROFIT REPORT", "RETROFIT", "REPAIR REPORT", "TEST REPORT")

OUT_OF_SCOPE_REASON = {
    "REPAIR REPORT": "정비 보고서 — 작업 기록이지 사양서가 아님(사양 칸 대부분 공란)",
    "RETROFIT REPORT": "개조 보고서 — 작업 기록이지 사양서가 아님",
    "RETROFIT": "개조 보고서 — 작업 기록이지 사양서가 아님",
    "TEST REPORT": "시험 보고서 — 사양서가 아님",
    "DRAWING": "도면 — 사양표가 아님",
}


def scope_reason(path: str) -> str:
    """제외 사유 문구. 대상이거나 판단 불가면 빈 문자열.

    Triage 가 `TriageResult.reason` 에 그대로 넣는다. 철학 5 — 왜 안 했는지를
    남긴다. 조용히 건너뛰면 처리 실패율과 구분되지 않는다.
    """
    info = parse_filename(path)
    if info.in_scope is not False:
        return ""
    return OUT_OF_SCOPE_REASON.get(info.doc_kind, f"{info.doc_kind} — 대상 아님")


# ── 아래는 파이프라인이 쓰지 않는다 — 코퍼스 통계용 ─────────────────
#
#  2026-08-24 범위 결정 (사용자): *"동일한 설비 파일이 여러개인데 이중에 뭐가
#  정답이지?는 이번 고민사항이 아니다"*. 이번 과제는 **개별 파일을 넣었을 때
#  추출이 되는가** 이고, 파일 간 권위 판단은 범위 밖이다.
#
#  그래서 아래 두 함수는 Triage 에서 호출하지 않는다. 코퍼스에 이런 구조적
#  문제가 얼마나 있는지 세는 용도로만 남긴다(발표 자료용).
#      대상 데이터시트 907건 중 정비·개조 보고서가 따로 있는 것  79건 (8.7%)
#      데이터시트가 아예 없고 보고서만 있는 태그               34건

def index_by_tag(paths: Iterable[str]) -> dict[str | None, list[FileNameInfo]]:
    """파일 목록을 태그별로 묶는다. 파일명만 보므로 비용이 없다."""
    out: dict[str | None, list[FileNameInfo]] = {}
    for p in paths:
        info = parse_filename(p)
        out.setdefault(info.tag, []).append(info)
    return out


def staleness_warning(path: str, index: dict) -> str:
    """이 태그에 정비·개조 보고서가 따로 있는가. 없으면 빈 문자열.

    ⚠️ 파이프라인에서 쓰지 않는다(위 범위 결정 참조). 코퍼스 통계용.
    """
    info = parse_filename(path)
    if not info.tag:
        return ""
    sibs = [s for s in index.get(info.tag, [])
            if os.path.normcase(s.path) != os.path.normcase(path)
            and s.doc_kind in REPORT_DOC_KINDS]
    if not sibs:
        return ""
    names = ", ".join(sorted({s.doc_kind for s in sibs}))
    return f"{info.tag} 에 {names} 가 별도로 있음 (파일 간 판단은 범위 밖)"


def probe_pages(path: str) -> list[PageText]:
    """페이지별로 텍스트 레이어가 있는지 본다.

    PDF 파일의 57.4% 가 텍스트·스캔 혼재이므로 파일 단위로 판정하면 안 된다.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in PDF_EXT:
        return _probe_pdf(path)
    if ext in IMAGE_EXT:
        return _probe_image(path)
    if ext in EXCEL_EXT:
        return _probe_excel(path)
    return []


def _probe_pdf(path: str) -> list[PageText]:
    import pymupdf
    pymupdf.TOOLS.mupdf_display_errors(False)
    out = []
    with pymupdf.open(path) as d:
        for i, pg in enumerate(d, start=1):
            t = pg.get_text() or ""
            out.append(PageText(
                page=i, text=t,
                has_text_layer=len(t.strip()) >= TEXT_LAYER_MIN,
                width=int(pg.rect.width), height=int(pg.rect.height)))
    return out


def _probe_image(path: str) -> list[PageText]:
    """이미지는 텍스트 레이어가 없다. 다중 페이지 TIF 를 놓치지 않는 것이 요점."""
    from PIL import Image
    out = []
    with Image.open(path) as im:
        n = getattr(im, "n_frames", 1)
        for i in range(n):
            im.seek(i)
            out.append(PageText(page=i + 1, has_text_layer=False,
                                width=im.size[0], height=im.size[1]))
    return out


def _probe_excel(path: str) -> list[PageText]:
    """엑셀은 셀 값이 곧 텍스트 레이어다. PDF 보다 정확하다 — 주소가 정확하니까."""
    ext = os.path.splitext(path)[1].lower()
    out = []
    if ext == ".xls":
        import xlrd
        book = xlrd.open_workbook(path)
        for i, sh in enumerate(book.sheets(), start=1):
            buf = []
            for r in range(sh.nrows):
                for c in range(sh.ncols):
                    v = sh.cell_value(r, c)
                    if v not in ("", None):
                        buf.append(str(v))
            txt = "\n".join(buf)
            out.append(PageText(page=i, text=txt,
                                has_text_layer=len(txt.strip()) >= TEXT_LAYER_MIN,
                                locator=sh.name))
    else:
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        for i, ws in enumerate(wb.worksheets, start=1):
            buf = []
            for row in ws.iter_rows(values_only=True):
                for v in row:
                    if v is not None and str(v).strip():
                        buf.append(str(v))
            txt = "\n".join(buf)
            out.append(PageText(page=i, text=txt,
                                has_text_layer=len(txt.strip()) >= TEXT_LAYER_MIN,
                                locator=ws.title))
        wb.close()
    return out


# ══════════════════════════════════════════════════════════════
#  페이지 렌더링 — 모든 포맷을 PNG 로
# ══════════════════════════════════════════════════════════════
#
#  JPG 가 아니라 PNG 를 쓴다. JPG 는 손실 압축이라 글자 주변에 잡티가 생기고,
#  1비트 150DPI 손글씨 스캔에서는 그 잡티가 판독을 방해한다.

def render_pages(path: str, out_dir: str, dpi: int = RENDER_DPI,
                 pages: Iterable[int] | None = None) -> list[str]:
    """페이지를 PNG 로 렌더링하고 경로 목록을 돌려준다.

    pages 를 주면 그 페이지만 렌더한다 (1부터). 사양표 한 장만 필요할 때
    나머지를 렌더하지 않아 시간과 디스크를 아낀다.
    """
    os.makedirs(out_dir, exist_ok=True)
    ext = os.path.splitext(path)[1].lower()
    want = set(pages) if pages else None

    if ext in PDF_EXT:
        return _render_pdf(path, out_dir, dpi, want)
    if ext in IMAGE_EXT:
        return _render_image(path, out_dir, want)
    if ext in EXCEL_EXT:
        return _render_excel(path, out_dir, dpi, want)
    raise ValueError(f"렌더링을 지원하지 않는 포맷: {ext}")


def _stem(path: str) -> str:
    s = os.path.splitext(os.path.basename(path))[0]
    return re.sub(r"[^\w.-]+", "_", s)[:70]


def _render_pdf(path, out_dir, dpi, want) -> list[str]:
    import pymupdf
    pymupdf.TOOLS.mupdf_display_errors(False)
    out = []
    with pymupdf.open(path) as d:
        for i, pg in enumerate(d, start=1):
            if want and i not in want:
                continue
            p = os.path.join(out_dir, f"{_stem(path)}_p{i:02d}.png")
            pg.get_pixmap(dpi=dpi).save(p)
            out.append(p)
    return out


def _render_image(path, out_dir, want) -> list[str]:
    """다중 페이지 TIF 를 전부 뽑는다. 1비트는 그레이스케일로 올려 판독을 돕는다."""
    from PIL import Image
    out = []
    with Image.open(path) as im:
        n = getattr(im, "n_frames", 1)
        for i in range(n):
            if want and (i + 1) not in want:
                continue
            im.seek(i)
            fr = im.convert("L") if im.mode == "1" else im.convert("RGB")
            p = os.path.join(out_dir, f"{_stem(path)}_p{i + 1:02d}.png")
            fr.save(p, "PNG")
            out.append(p)
    return out


def _render_excel(path, out_dir, dpi, want) -> list[str]:
    """엑셀 → PDF → PNG. 시트 1개 = PNG 1장으로 맞춘다.

    통째로 내보내면 5시트가 인쇄면 13장이 되어 probe_pages()의 시트 번호와
    어긋난다(has_text_layer 는 시트 기준, render_path 는 인쇄면 기준).
    엑셀의 의미 단위는 인쇄면이 아니라 시트이므로 시트마다 따로 내보낸다.

    실패하면 예외를 던지지 않고 빈 목록을 돌려준다(Triage 가 판정하게).
    """
    try:
        import win32com.client as win32
        import pymupdf
        pymupdf.TOOLS.mupdf_display_errors(False)
    except ImportError:
        return []

    out, tmps = [], []
    excel = None
    try:
        excel = win32.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        wb = excel.Workbooks.Open(os.path.abspath(path), ReadOnly=True)
        try:
            for idx in range(1, wb.Worksheets.Count + 1):
                if want and idx not in want:
                    continue
                sh = wb.Worksheets(idx)
                ps = sh.PageSetup
                ps.Zoom = False
                ps.FitToPagesWide = 1      # 한 장에 다 담아 시트=페이지를 보장
                ps.FitToPagesTall = 1
                ps.Orientation = 2         # 가로
                ps.PrintArea = ""          # 저장된 인쇄영역이 표를 자르는 것을 막는다
                tmp = os.path.join(out_dir, f"{_stem(path)}_s{idx:02d}.pdf")
                sh.ExportAsFixedFormat(0, os.path.abspath(tmp))
                if not os.path.exists(tmp):
                    continue
                tmps.append(tmp)
                png = os.path.join(out_dir, f"{_stem(path)}_p{idx:02d}.png")
                with pymupdf.open(tmp) as d:
                    if d.page_count:
                        d[0].get_pixmap(dpi=dpi).save(png)
                        out.append(png)
        finally:
            wb.Close(False)
    except Exception:
        return []
    finally:
        if excel is not None:
            try:
                excel.Quit()
            except Exception:
                pass
        for t in tmps:
            try:
                os.remove(t)
            except OSError:
                pass
    return out


# ══════════════════════════════════════════════════════════════
#  최신성 — 사양표가 2장 이상일 때 어느 쪽이 최신인가
# ══════════════════════════════════════════════════════════════
#
#  왜 필요한가 (2026-08-24, 10FV011 실물 확인)
#    `10FV011-DATA SHEET_REV1.tif` 는 사양표가 2장이다.
#      p1  2003/03/25  Valstone  "CONTROL VALVE RETROFIT"  태그 011/012/013/014
#      p4  1986/09/06  Fisher    수기 "OLD"                태그 011 단독
#    p4 를 고르면 MODEL NO.(657-ED ↔ 667-ED)와 RATED CV(70.7 ↔ 95) 가 틀린다.
#    같은 파일의 준공검사보고서(CVT-030526-1)가 p1 값을 확증한다.
#    → 최신 페이지를 고른다. 태그 단독성은 선택 기준이 아니다.

# 폐기 표기 — 사람이 이미 손으로 표시해둔 것을 존중한다
SUPERSEDED_MARKS = [
    (r"\bOLD\b", "OLD"),
    (r"\bSUPERSED", "SUPERSEDED"),
    (r"\bVOID\b", "VOID"),
    (r"\bCANCELL?ED\b", "CANCELLED"),
    (r"구\s*버전|폐기", "폐기"),
]

# 개정 성격 표기 — 날짜가 없거나 같을 때의 보조 근거
REVISION_MARKS = [
    (r"RETROFIT", "RETROFIT"),
    (r"REVISED|REVISION", "REVISED"),
    (r"AS[\s-]*BUILT", "AS-BUILT"),
]

_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}


@dataclass
class DocDate:
    raw: str = ""
    year: int | None = None
    month: int | None = None
    day: int | None = None
    ambiguous: bool = False     # 월·일 순서를 가릴 수 없음 (9/6/1986)

    @property
    def key(self) -> tuple:
        """정렬용. 연도만 있어도 비교 가능하도록 없는 자리는 0."""
        return (self.year or 0, self.month or 0, self.day or 0)

    def __bool__(self) -> bool:
        return self.year is not None


def parse_doc_date(text: str) -> DocDate:
    """문서에 적힌 날짜를 읽는다. **4자리 연도가 없으면 날짜로 보지 않는다.**

    두 자리 연도를 허용하면 견적번호 `85-1874` · `REV1` · 팩스 헤더 `03-04`
    가 전부 날짜로 잡힌다. 실제로 10FV011 p4 에는 `85-1874`(견적번호)와
    `9/6/1986`(날짜)이 같은 헤더에 나란히 있다. 연도 4자리를 요구하면
    후자만 잡힌다 — 놓치는 편이 잘못 잡는 편보다 낫다(철학 4).

    월·일 순서(9/6 = 9월 6일인가 6월 9일인가)는 양식마다 달라 가릴 수 없다.
    `ambiguous=True` 로 표시하고, 비교는 연도부터 한다.
    """
    if not text:
        return DocDate()
    up = unicodedata.normalize("NFKC", str(text)).upper()

    # 2003/03/25 · 2003-03-25 · 2003.03.25 (연도 먼저 — 순서 확실)
    m = re.search(r"(19|20)(\d{2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{1,2})", up)
    if m:
        y = int(m.group(1) + m.group(2))
        return DocDate(m.group(0).strip(), y, int(m.group(3)), int(m.group(4)))

    # SEP. 11, 2015 · 11 SEP 2015 (월 이름 — 순서 확실)
    m = re.search(r"([A-Z]{3})[A-Z]*\.?\s+(\d{1,2})\s*,?\s*((?:19|20)\d{2})", up)
    if m and m.group(1) in _MONTHS:
        return DocDate(m.group(0).strip(), int(m.group(3)),
                       _MONTHS[m.group(1)], int(m.group(2)))
    m = re.search(r"(\d{1,2})\s+([A-Z]{3})[A-Z]*\.?\s*,?\s*((?:19|20)\d{2})", up)
    if m and m.group(2) in _MONTHS:
        return DocDate(m.group(0).strip(), int(m.group(3)),
                       _MONTHS[m.group(2)], int(m.group(1)))

    # 9/6/1986 — 앞 두 자리 순서를 가릴 수 없다
    m = re.search(r"(\d{1,2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*((?:19|20)\d{2})", up)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        amb = a <= 12 and b <= 12 and a != b
        mo, dy = (a, b) if b > 12 else (b, a) if a > 12 else (a, b)
        return DocDate(m.group(0).strip(), int(m.group(3)), mo, dy, amb)

    # 연도만 (2003년, ' 2003 ')
    m = re.search(r"\b((?:19|20)\d{2})\b", up)
    if m:
        return DocDate(m.group(0), int(m.group(1)))
    return DocDate()


def find_marks(text: str) -> tuple[str, bool]:
    """개정 성격 표기와 폐기 여부를 읽는다. → (revision_marker, superseded)"""
    if not text:
        return "", False
    up = unicodedata.normalize("NFKC", str(text)).upper()
    for pat, name in SUPERSEDED_MARKS:
        if re.search(pat, up):
            return name, True
    for pat, name in REVISION_MARKS:
        if re.search(pat, up):
            return name, False
    return "", False


def pick_latest_spec(pages, file_tag: str | None = None):
    """사양표 후보 중 최신 한 장을 고른다. → (선택된 PageInfo | None, 사유)

    Triage 가 쓰는 기본 정책이다. 결정론적이고 fixture 로 검증 가능하다.
    고르지 못하면 **아무거나 고르지 않고 None 을 돌려준다** — 사람이 고른다.

    순서
      1) 폐기 표기("OLD" 등)가 있는 후보를 먼저 버린다 (사람이 이미 표시함)
      2) 남은 것 중 연도가 가장 늦은 것
      3) 연도가 같으면 월·일. 단 순서가 모호한 날짜끼리는 비교하지 않는다
      4) 날짜로 못 가리면 RETROFIT / AS-BUILT / REVISED 표기가 있는 쪽
      5) 그래도 못 가리면 None — 사람이 고른다

    file_tag 은 **선택에 쓰지 않는다.** 고른 뒤 그 페이지에 이 태그가
    보이는지 검증하는 데만 쓴다(사유 문구에 반영).
    """
    cands = [p for p in pages if getattr(p, "is_spec", False)]
    if not cands:
        return None, "사양표 페이지 없음"
    if len(cands) == 1:
        return cands[0], f"사양표 1장 (p{cands[0].page})"

    live = [p for p in cands if not p.superseded]
    dropped = [p for p in cands if p.superseded]
    note = ""
    if dropped:
        note = " / 폐기표기 제외: " + ", ".join(
            f"p{p.page}({p.revision_marker})" for p in dropped)
    if not live:
        return None, f"후보 전부 폐기 표기 — 사람이 확인{note}"
    if len(live) == 1:
        return live[0], f"폐기 표기 없는 유일한 사양표 (p{live[0].page}){note}"

    dated = [p for p in live if p.date_key and p.date_key[0]]
    if dated:
        top = max(p.date_key[0] for p in dated)
        newest = [p for p in dated if p.date_key[0] == top]
        if len(newest) == 1:
            p = newest[0]
            others = ", ".join(f"p{q.page}({q.doc_date or '날짜없음'})"
                               for q in live if q is not p)
            return p, f"최신 사양표 p{p.page} ({p.doc_date}) — 대비 {others}{note}"
        # 같은 연도 → 월·일. 모호한 날짜가 섞이면 비교하지 않는다
        if not any(p.date_ambiguous for p in newest):
            best = max(newest, key=lambda p: p.date_key)
            ties = [p for p in newest if p.date_key == best.date_key]
            if len(ties) == 1:
                return best, f"최신 사양표 p{best.page} ({best.doc_date}){note}"
        live = newest

    for want in ("AS-BUILT", "RETROFIT", "REVISED"):
        hit = [p for p in live if p.revision_marker == want]
        if len(hit) == 1:
            return hit[0], (f"날짜로 가리지 못해 개정 표기로 선택 — "
                            f"p{hit[0].page} ({want}){note}")

    pages_txt = ", ".join(f"p{p.page}({p.doc_date or '날짜없음'})" for p in live)
    return None, f"최신 판정 불가 — 사람이 선택: {pages_txt}{note}"


# ══════════════════════════════════════════════════════════════
#  격자 합성 — VLM 이진 판정을 한 번에
# ══════════════════════════════════════════════════════════════

def make_montage(png_paths: list[str], out_path: str, cols: int = 3,
                 cell: int = 480, label: bool = True,
                 max_cells: int = 12) -> list[str]:
    """썸네일을 격자로 합쳐 판정용 시트를 만든다. 시트 경로 목록을 돌려준다.

    8페이지를 VLM 8회가 아니라 1회로 판정하기 위한 것. 실제 8페이지 파일로
    확인한 결과 480px 썸네일에서 사양표·도면·부품리스트·표지·계산서가
    구분된다. 단 태그 글자는 이 크기에서 읽히지 않는다 — 종류 판정까지만
    쓰고, 태그 대조는 원본 해상도에서 따로 한다.

    max_cells 로 나눠 담는 이유: Claude 는 긴 변 1568px 를 넘으면 이미지를
    줄인다. 61페이지를 한 장에 담으면 셀이 224px 로 줄어 판독이 불가능해진다.
    12칸(3x4=1440px)이 그 한계 안에 들어가는 최대치다.
    """
    from PIL import Image, ImageDraw
    n = len(png_paths)
    if n == 0:
        raise ValueError("렌더된 페이지가 없습니다")

    root, ext = os.path.splitext(out_path)
    ext = ext or ".png"
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    sheets = []
    batches = [png_paths[i:i + max_cells] for i in range(0, n, max_cells)]
    for bi, batch in enumerate(batches):
        c = max(1, min(cols, len(batch)))
        rows = (len(batch) + c - 1) // c
        sheet = Image.new("RGB", (c * cell, rows * cell), (255, 255, 255))
        draw = ImageDraw.Draw(sheet)
        for i, p in enumerate(batch):
            with Image.open(p) as im:
                t = im.convert("RGB")
                t.thumbnail((cell - 10, cell - 26))
                x, y = (i % c) * cell + 5, (i // c) * cell + 22
                sheet.paste(t, (x, y))
            if label:
                # 칸 번호는 파일 전체 기준 페이지 번호 — VLM 이 번호로 답한다
                pno = bi * max_cells + i + 1
                bx, by = (i % c) * cell + 5, (i // c) * cell + 3
                draw.rectangle([bx, by, bx + 54, by + 17], fill=(31, 78, 107))
                draw.text((bx + 6, by + 4), f"p{pno}", fill=(255, 255, 255))
            draw.rectangle([(i % c) * cell + 2, (i // c) * cell + 2,
                            (i % c + 1) * cell - 2, (i // c + 1) * cell - 2],
                           outline=(200, 200, 200))
        out = f"{root}{ext}" if len(batches) == 1 else f"{root}_{bi + 1}{ext}"
        sheet.save(out, "PNG")
        sheets.append(out)
    return sheets


# ══════════════════════════════════════════════════════════════
#  자체 확인
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys, glob, tempfile
    sys.stdout.reconfigure(encoding="utf-8")

    print("=" * 72)
    print("  전처리 유틸리티 자체 확인")
    print("=" * 72)

    print("\n[1] 태그 정규화 — 파일명과 문서 표기가 같은 값이 되는가")
    for a, b in [("10FV012", "10-FV-012"), ("10FV007B", "10-FV-007B"),
                 ("10PV002", "10-PV-002"), ("10FV002", "10 FV 002")]:
        na, nb = normalize_tag(a), normalize_tag(b)
        ok = "OK  " if na == nb and na else "실패"
        print(f"  {ok} {a:<12} vs {b:<14} → {na} / {nb}")

    print("\n[2] 파일명 파싱 — 실제 코퍼스")
    names = sorted({os.path.normcase(p) for p in glob.glob("raw_file/*")})
    if names:
        stat = {"대상": 0, "제외": 0, "판단불가": 0, "태그없음": 0}
        for p in names:
            i = parse_filename(p)
            stat["대상" if i.in_scope else "제외" if i.in_scope is False
                 else "판단불가"] += 1
            if not i.tag:
                stat["태그없음"] += 1
        tot = len(names)
        for k, v in stat.items():
            print(f"  {k:<10}{v:>5}건  {v/tot*100:>5.1f}%")
        print("\n  예시:")
        for p in names[:3] + [p for p in names if "10fv012" in p.lower()][:1]:
            i = parse_filename(p)
            print(f"    {os.path.basename(p)[:44]:<46} tag={i.tag} "
                  f"kind={i.doc_kind or '-'} scope={i.in_scope} {i.rev}")

    print("\n[3] 페이지 탐침 + 렌더링 — 다중 페이지 TIF")
    target = next((p for p in names if "10fv012" in p.lower()
                   and p.lower().endswith(".tif")), None)
    if target:
        pt = probe_pages(target)
        print(f"  {os.path.basename(target)} — {len(pt)}페이지")
        for x in pt[:3]:
            print(f"    p{x.page}: {x.width}x{x.height} 텍스트레이어={x.has_text_layer} "
                  f"{x.text_len}자")
        tmp = os.path.join(tempfile.gettempdir(), "d2s_render_check")
        pngs = render_pages(target, tmp, pages=[1, 4])
        print(f"  선택 렌더 (p1, p4): {len(pngs)}장")
        for p in pngs:
            print(f"    {os.path.basename(p)}  {os.path.getsize(p):,} bytes")
        allp = render_pages(target, tmp)
        grid = make_montage(allp, os.path.join(tmp, "montage.png"))
        print(f"  격자 합성: {len(allp)}페이지 → {len(grid)}장 "
              f"({', '.join(os.path.basename(g) for g in grid)})")

    print("\n[4] 엑셀 렌더링 실현성 — Excel COM")
    xl = next((p for p in names if p.lower().endswith(".xlsx")
               and "DATA SHEET" in p.upper()), None)
    if xl:
        tmp = os.path.join(tempfile.gettempdir(), "d2s_render_check")
        pngs = render_pages(xl, tmp)
        if pngs:
            print(f"  OK   {os.path.basename(xl)[:40]} → {len(pngs)}장")
            for p in pngs[:3]:
                from PIL import Image
                with Image.open(p) as im:
                    print(f"       {os.path.basename(p)}  {im.size}  "
                          f"{os.path.getsize(p):,} bytes")
        else:
            print(f"  실패 {os.path.basename(xl)[:40]} — Excel COM 사용 불가")
            print("       → 엑셀 경로는 텍스트 파싱만으로 가야 함 (화면에 원본 표시 불가)")
    else:
        print("  대상 엑셀 없음")
    print()
