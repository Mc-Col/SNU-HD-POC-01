# -*- coding: utf-8 -*-
"""
모듈 간 계약 — 이 파일은 이종수 책임만 수정한다.

모듈 사이를 넘는 데이터는 아래 세 형태만 존재한다.

    파일 ─▶ TriageResult ─▶ RawExtraction ─▶ FieldRecord ─▶ HITL · 로그 · 평가

각 모듈은 자기 입력 계약을 받아 자기 출력 계약을 반환하면 된다.
남의 모듈이 내부에서 무엇을 하는지 알 필요가 없다.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


# ══════════════════════════════════════════════════════════════════
#  열거형
# ══════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════
#  bbox 규약 — 화면·파서·평가가 모두 이 규약을 따른다
#
#      (x0, y0, x1, y1)   정규화 좌표 0.0 ~ 1.0
#      원점은 페이지 좌상단, x 는 오른쪽, y 는 아래쪽이 증가
#      페이지 번호는 별도 필드(page, 1부터)
#
#  정규화로 두는 이유 — 해상도·DPI 가 바뀌어도 값이 유효하고, 화면이 %
#  로 바로 쓸 수 있다. 픽셀로 두면 렌더 배율마다 어긋난다.
# ══════════════════════════════════════════════════════════════════

BBox = tuple[float, float, float, float]


class PageClass(str, Enum):
    """페이지 단위 이진 판정 결과.

    로직은 SPEC 여부만 쓴다. 나머지 값은 화면에 "왜 제외됐나" 를 보여주기
    위한 표시용이며, 같은 VLM 호출에서 공짜로 얻는다.
    """
    SPEC = "spec"              # 밸브 사양표 — 처리 대상
    DRAWING = "drawing"        # 도면
    BOM = "bom"                # 부품 리스트
    COVER = "cover"            # 표지·송부
    CALC = "calc"              # 계산서
    OTHER = "other"            # 그 외


class DocumentClass(str, Enum):
    """① Triage 의 판정 결과."""
    DATASHEET = "datasheet"                    # 정상 처리 대상
    DATASHEET_EMBEDDED = "datasheet_embedded"  # 다른 문서에 첨부됨 → 해당 페이지만
    OUT_OF_SCOPE = "out_of_scope"              # 대상 아님 (배제 + 사유 로깅)
    UNSUPPORTED = "unsupported"                # 미지원 포맷 (예외 없이 기록)


class ParserType(str, Enum):
    """② Router 가 선택한 경로."""
    EXCEL = "excel"          # xlsx / xlsm / xls
    PDF_TEXT = "pdf_text"    # 텍스트 레이어 있는 PDF
    VLM = "vlm"              # 스캔 PDF / tif


class FieldState(str, Enum):
    """⑥ State 의 판정 결과. 자동확정 외에는 사람이 해소해야 승인 가능."""
    AUTO = "auto"        # 자동확정 — 일괄 승인 가능
    REVIEW = "review"    # 리뷰필요 — 개별 확인 필수
    NA = "na"            # 문서에 근거 없음 — 사람이 채움


class FailureKind(str, Enum):
    """검증 실패 유형. 재시도 가능 여부를 결정하는 핵심 구분."""
    NONE = "none"
    EXTRACTION = "extraction"    # 못 읽음 → 재시도 가능
    CONSTRAINT = "constraint"    # 읽었으나 값이 이상 → 재시도 금지, 사람에게
    FORMAT = "format"            # 형식·허용값 위반 → 재시도 금지
    NO_EVIDENCE = "no_evidence"  # 문서에 근거 없음 → N/A


# ══════════════════════════════════════════════════════════════════
#  계약 ① TriageResult   (① Triage → ② Router)
# ══════════════════════════════════════════════════════════════════

@dataclass
class PageInfo:
    """페이지 하나의 판정 결과. 화면이 "왜 이 페이지를 제외했나" 를 보여줄 근거.

    MVP 원칙 — 파일 하나에 자산 하나. 사양표가 여러 장이면 파일명 태그가
    단독으로 나오는 페이지 하나만 고르고 나머지는 보지 않는다(토큰 절약).
    발견 사실은 로그에만 남긴다.
    """
    page: int                          # 1부터
    page_class: PageClass = PageClass.OTHER
    kind_hint: str = ""                # 표시용 자유 문구 ("부품 리스트" 등)
    confidence: float = 0.0
    tags: list[str] = field(default_factory=list)   # 이 페이지에 보이는 태그
    has_text_layer: bool = False       # 텍스트 레이어 유무 (페이지 단위)
    text_len: int = 0
    render_path: str | None = None     # 렌더된 PNG 경로 — 화면이 표시에 사용
    selected: bool = False             # 처리 대상으로 고른 페이지인가
    reason: str = ""                   # 선택·제외 사유

    @property
    def is_spec(self) -> bool:
        return self.page_class is PageClass.SPEC


@dataclass
class Target:
    """처리 대상 위치. MVP 는 1파일 = 1자산 전제이므로 보통 한 개."""
    page_from: int = 1
    page_to: int = 1
    sheet: str | None = None
    region: tuple[float, float, float, float] | None = None  # x0,y0,x1,y1
    expected_tag_count: int = 1


@dataclass
class TriageResult:
    source_path: str
    document_class: DocumentClass
    targets: list[Target] = field(default_factory=list)
    confidence: float = 0.0
    # ── 파일명에서 얻은 것 (전체의 95.5% 가 여기서 무료로 나온다) ──
    file_tag: str | None = None        # 파일명의 태그 — 주 자산의 식별자
    file_doc_kind: str = ""            # 파일명의 문서 종류 (DATA SHEET 등)
    file_rev: str = ""                 # REV0 / REV1 …
    # ── 페이지 판정 ──
    pages: list[PageInfo] = field(default_factory=list)
    extra_assets: list[str] = field(default_factory=list)
    #   다중 사양표에서 발견했으나 이번에 처리하지 않은 태그.
    #   화면에는 띄우지 않고 로그로만 남긴다 (2026-08-23 결정).
    #   "1,089건 중 N건이 다중 자산" 이라는 본개발 근거가 여기서 나온다.
    reason: str = ""                    # 판정 근거. out_of_scope 일 때 필수
    stats: dict[str, Any] = field(default_factory=dict)  # 페이지수·표수·시트수 등

    @property
    def processable(self) -> bool:
        return self.document_class in (
            DocumentClass.DATASHEET, DocumentClass.DATASHEET_EMBEDDED
        )

    @property
    def selected_page(self) -> PageInfo | None:
        """처리 대상으로 고른 페이지. 없으면 None(= 사양표를 못 찾음)."""
        return next((p for p in self.pages if p.selected), None)

    @property
    def spec_pages(self) -> list[PageInfo]:
        return [p for p in self.pages if p.is_spec]

    @property
    def excluded_pages(self) -> list[PageInfo]:
        """화면이 "이 페이지는 왜 빠졌나" 를 보여줄 목록."""
        return [p for p in self.pages if not p.selected]

    @property
    def no_spec_found(self) -> bool:
        """사양표가 한 장도 없음 → 화면이 전용 안내를 띄워야 하는 경우."""
        return bool(self.pages) and not self.spec_pages


# ══════════════════════════════════════════════════════════════════
#  계약 ② RawExtraction   (③ Parser → ④ Normalize)
# ══════════════════════════════════════════════════════════════════

@dataclass
class RawExtraction:
    """파서가 문서에서 읽어낸 그대로. 정규화·검증 전 상태."""
    field_key: str                       # schema/fields.yaml 의 key
    raw_value: str | None                # 문서에 적힌 값 그대로. 없으면 None
    raw_label: str | None = None         # 문서에 적힌 항목명 그대로
                                         #   → 유사표현 사전을 자동으로 키우는 필드
    bbox: tuple[float, float, float, float] | None = None   # 페이지 좌표
    page: int = 1
    confidence: float = 0.0
    parser: ParserType = ParserType.EXCEL
    source_locator: str = ""             # "Sheet1!C7" / "p2:table1:r3c2" 등 재현용
    note: str = ""                       # 파서가 남기는 관찰 (예: 셀 병합, 흐림)

    @property
    def found(self) -> bool:
        return self.raw_value is not None and str(self.raw_value).strip() != ""


# ══════════════════════════════════════════════════════════════════
#  계약 ③ FieldRecord   (⑥ State → HITL · 로그 · 평가)
# ══════════════════════════════════════════════════════════════════

@dataclass
class FieldRecord:
    """확정된 필드 한 개. UI·로그·평가가 모두 이 형태만 소비한다."""
    # 식별
    doc_id: str
    field_key: str
    field_name: str

    # 값
    value: str | None                    # 정규화된 표준값. N/A 면 None
    raw_value: str | None = None         # 문서 원문 (앵커링 방지용 병기)
    raw_label: str | None = None

    # 판정
    state: FieldState = FieldState.REVIEW
    failure: FailureKind = FailureKind.NONE
    note: str = ""                       # AUTO 외 상태에서는 필수
    confidence: float = 0.0
    threshold: float = 0.90              # 이 필드에 적용된 임계값

    # 근거
    bbox: tuple[float, float, float, float] | None = None
    page: int = 1
    source_locator: str = ""
    transform_trace: list[str] = field(default_factory=list)
    #   예: ["원문 'Air-to-Open (ATO)'",
    #        "규칙 ATO → 공기 상실 시 스프링 폐쇄",
    #        "결과 Fail Close",
    #        "교차검증 ACTUATOR TYPE=Pneumatic 정합"]

    # 메타
    required: bool = False
    safety: str = "normal"               # safety / identity / normal
    parser: ParserType = ParserType.EXCEL
    retry_count: int = 0
    retry_values: list[str] = field(default_factory=list)   # 1·2차 비교용
    elapsed_ms: int = 0

    # 사람의 판단 (HITL 이후 채워짐)
    human_value: str | None = None
    human_action: str | None = None       # approve / override / na_confirm
    approved_by: str | None = None

    # ── 불변식 ──────────────────────────────────────────────
    def validate(self) -> None:
        """계약 위반을 조용히 넘기지 않는다. 하네스가 호출한다."""
        if self.state is not FieldState.AUTO and not self.note.strip():
            raise ValueError(
                f"[{self.field_key}] state={self.state.value} 인데 note 가 비어 있음. "
                f"자동확정 외 상태는 사유 기재가 필수임."
            )
        if self.state is FieldState.NA and self.value not in (None, "", "N/A"):
            raise ValueError(f"[{self.field_key}] state=na 인데 값이 있음: {self.value!r}")
        if self.safety in ("safety", "identity") and self.state is FieldState.AUTO:
            # 안전·식별 필드는 자동확정이어도 사람 확인이 필요함을 표시
            if self.human_action is None:
                self.note = (self.note + " | 사람 확인 필요(안전·식별 필드)").strip(" |")

    @property
    def final_value(self) -> str | None:
        """사람이 고쳤으면 그 값이 최종."""
        return self.human_value if self.human_value is not None else self.value

    @property
    def resolved(self) -> bool:
        """승인 가능한 상태인가. 미해소 필드가 있으면 건 전체 승인이 막힌다."""
        if self.state is FieldState.AUTO and self.safety == "normal":
            return True
        return self.human_action is not None

    def to_row(self) -> dict[str, Any]:
        """JSONL 한 줄로."""
        d = asdict(self)
        for k in ("state", "failure", "parser"):
            v = d.get(k)
            if isinstance(v, Enum):
                d[k] = v.value
            elif v is not None and hasattr(v, "value"):
                d[k] = v.value
        return d


# ══════════════════════════════════════════════════════════════════
#  문서 단위 묶음
# ══════════════════════════════════════════════════════════════════

@dataclass
class DocumentResult:
    doc_id: str
    source_path: str
    triage: TriageResult
    records: list[FieldRecord] = field(default_factory=list)
    elapsed_ms: int = 0
    error: str | None = None

    @property
    def approvable(self) -> bool:
        """필수 필드가 모두 해소되었는가. UI 의 '검토 완료' 활성화 조건."""
        return all(r.resolved for r in self.records if r.required)

    def counts(self) -> dict[str, int]:
        c = {s.value: 0 for s in FieldState}
        for r in self.records:
            c[r.state.value] += 1
        return c
