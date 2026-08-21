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
    reason: str = ""                    # 판정 근거. out_of_scope 일 때 필수
    stats: dict[str, Any] = field(default_factory=dict)  # 페이지수·표수·시트수 등

    @property
    def processable(self) -> bool:
        return self.document_class in (
            DocumentClass.DATASHEET, DocumentClass.DATASHEET_EMBEDDED
        )


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
