# -*- coding: utf-8 -*-
"""화면에 들어올 `DocumentResult` 를 공급한다.

원천이 둘이다.

    fixture   fixtures/ui/sample_document_result.json  — 모듈이 없어도 화면을 검증한다
    pipeline  Pipeline().run_document(path)            — 모듈이 붙으면 이쪽으로 전환

둘 다 같은 계약을 돌려주므로 화면 코드는 어느 쪽인지 모른다. 그게 전환 비용이 0인 이유다.

픽스처에는 값·원문·bbox·확신도·상태만 있다. required·safety·threshold·field_name 은
schema/fields.yaml 에서 채운다 — 픽스처가 스키마와 어긋나면 여기서 예외가 난다.
"""
from __future__ import annotations

import io
import json
import os
from dataclasses import dataclass

from src import schema
from src.contracts import (
    DocumentClass, DocumentResult, FailureKind, FieldRecord, FieldState,
    ParserType, Target, TriageResult,
)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DIR = os.path.join(ROOT, "fixtures", "ui")
FIXTURE_JSON = os.path.join(FIXTURE_DIR, "sample_document_result.json")
FIXTURE_PDF = os.path.join(FIXTURE_DIR, "sample_page.pdf")


@dataclass
class UiDoc:
    """화면이 필요한 것 = 계약 + 표시용 부속물 몇 개.

    계약(`DocumentResult`)을 늘리지 않기 위해 표시용 값은 이 껍데기에 둔다.
    """
    result: DocumentResult
    display_name: str
    page_path: str | None
    size_bytes: int
    route_reason: str
    origin: str                      # "fixture" | "pipeline"

    @property
    def records(self) -> list[FieldRecord]:
        return self.result.records

    def record(self, key: str) -> FieldRecord | None:
        return next((r for r in self.records if r.field_key == key), None)

    @property
    def unresolved_required(self) -> list[FieldRecord]:
        return [r for r in self.records if r.required and not r.resolved]


# ── 픽스처 ────────────────────────────────────────────────────

def _state(s: str) -> FieldState:
    return FieldState(str(s or "review").lower())


def _failure(s: str | None) -> FailureKind:
    return FailureKind(str(s or "none").lower())


def from_fixture(path: str | None = None) -> UiDoc:
    path = path or FIXTURE_JSON
    with io.open(path, encoding="utf-8") as f:
        fx = json.load(f)

    doc_id = fx["doc_id"]
    ptype = ParserType(fx.get("parser", "vlm"))
    stats = dict(fx.get("triage", {}).get("stats", {}))
    stats["page_size"] = fx.get("page_size")

    tri = TriageResult(
        source_path=FIXTURE_PDF,
        document_class=DocumentClass(fx["triage"]["document_class"]),
        targets=[Target()],
        confidence=float(fx["triage"].get("confidence", 0.0)),
        reason=fx["triage"].get("reason", ""),
        stats=stats,
    )

    records: list[FieldRecord] = []
    for fd in fx["fields"]:
        f = schema.get(fd["key"])          # ← 스키마에 없는 키면 여기서 멈춘다
        bbox = fd.get("bbox")
        rec = FieldRecord(
            doc_id=doc_id,
            field_key=f.key,
            field_name=f.name,
            value=fd.get("value"),
            raw_value=fd.get("raw_value"),
            raw_label=fd.get("raw_label"),
            state=_state(fd.get("state")),
            failure=_failure(fd.get("failure")),
            note=fd.get("note", ""),
            confidence=float(fd.get("confidence", 0.0)),
            threshold=f.threshold,          # ← 코드·픽스처가 아니라 fields.yaml
            bbox=tuple(bbox) if bbox else None,
            page=int(fd.get("page", 1)),
            source_locator=fd.get("source_locator", ""),
            transform_trace=list(fd.get("transform_trace", [])),
            required=f.required,            # ← fields.yaml
            safety=f.safety,                # ← fields.yaml
            parser=ptype,
            retry_count=int(fd.get("retry_count", 0)),
            retry_values=list(fd.get("retry_values", [])),
        )
        rec.validate()                      # 비고 누락·N/A 에 값 있음 → 예외
        records.append(rec)

    result = DocumentResult(
        doc_id=doc_id, source_path=FIXTURE_PDF, triage=tri,
        records=records, elapsed_ms=int(fx.get("elapsed_ms", 0)),
    )
    return UiDoc(
        result=result,
        display_name=fx.get("source_name", os.path.basename(FIXTURE_PDF)),
        page_path=FIXTURE_PDF,
        size_bytes=int(fx.get("source_bytes", 0)),
        route_reason=fx.get("route", {}).get("reason", ""),
        origin="fixture",
    )


# ── 실제 파이프라인 ───────────────────────────────────────────

def from_pipeline(path: str, *, only_mvp: bool = True, use_vlm: bool = True) -> UiDoc:
    """모듈이 붙으면 이 경로로 온다. 지금은 파서가 비어 있어 전부 N/A 로 나온다 —
    그게 정직한 현재 상태이고, 화면은 그것도 그대로 보여준다."""
    from src.pipeline import Pipeline

    p = Pipeline(only_mvp=only_mvp, use_vlm=use_vlm)
    result = p.run_document(path)
    ext = os.path.splitext(path)[1].lower()
    return UiDoc(
        result=result,
        display_name=os.path.basename(path),
        page_path=path if ext == ".pdf" else None,
        size_bytes=os.path.getsize(path) if os.path.exists(path) else 0,
        route_reason=result.triage.reason,
        origin="pipeline",
    )


def ensure_fixture_page() -> str:
    """합성 지면이 없으면 만든다. 픽스처에서 생성되므로 bbox 와 항상 일치한다."""
    if not os.path.exists(FIXTURE_PDF):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_make_sample", os.path.join(FIXTURE_DIR, "make_sample.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.build()
    return FIXTURE_PDF
