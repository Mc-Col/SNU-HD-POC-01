# -*- coding: utf-8 -*-
"""
하네스 — 이 파일은 이종수 책임만 수정한다.

세 겹의 루프를 구현한다.

    Loop A · 초   필드 재시도    추출 실패한 필드만 다시 읽는다 (최대 2회)
    Loop B · 분   문서 배치      문서를 순차 처리하며 필드 단위 로그를 남긴다
    Loop C · 일   규칙 개선      eval/harness 가 담당 (이 파일 밖)

모듈이 하나도 없어도 지금 당장 돌아간다. 각 슬롯에 기본 구현이 꽂혀 있고,
완성된 모듈로 하나씩 갈아끼우면 된다.

    python -m src.pipeline --smoke          모듈 없이 합성 문서로 전 구간 확인
    python -m src.pipeline <파일...>         실제 파일 처리

모듈 붙이는 방법:

    from src.pipeline import Pipeline
    from src.triage import MyTriage

    p = Pipeline(triage=MyTriage())          # 나머지는 기본 구현 유지
    result = p.run_document("raw_file/19-FV-001.pdf")
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from dataclasses import replace
from typing import Any, Protocol, Sequence

from src import models, schema
from src.contracts import (
    DocumentClass, DocumentResult, FailureKind, FieldRecord, FieldState,
    ParserType, RawExtraction, Target, TriageResult,
)
from src.hooks import hooks
from src.schema import Field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

EXCEL_EXT = {".xlsx", ".xlsm", ".xls"}
IMAGE_EXT = {".tif", ".tiff", ".jpg", ".jpeg", ".png"}
SUPPORTED = EXCEL_EXT | IMAGE_EXT | {".pdf"}


# ══════════════════════════════════════════════════════════════
#  모듈 인터페이스 — 각자 이 형태로 만들면 그대로 꽂힌다
# ══════════════════════════════════════════════════════════════

class TriageModule(Protocol):
    def run(self, path: str) -> TriageResult: ...


class RouterModule(Protocol):
    def run(self, triage: TriageResult) -> tuple[ParserType, str, dict[str, Any]]:
        """(파서 종류, 판정 근거, 통계) 를 반환한다."""


class ParserModule(Protocol):
    def extract(self, path: str, triage: TriageResult,
                fields: Sequence[Field]) -> list[RawExtraction]: ...

    def reread(self, path: str, f: Field, prev: RawExtraction,
               attempt: int = 1) -> RawExtraction | None:
        """Loop A — bbox 크롭만 재판독. 지원하지 않으면 None 을 반환한다.

        attempt 로 모델 단계를 고른다 — `models.for_attempt(attempt)`.
        1차는 싼 모델(luna), 재시도는 상위 모델(terra)이고 **못 읽은 필드
        하나만** 넘어오므로 상위 모델 비용은 그 한 필드분이다.
        """


class NormalizeModule(Protocol):
    def run(self, ex: RawExtraction, f: Field) -> tuple[str | None, list[str]]:
        """(표준값, transform_trace) 를 반환한다."""


class ValidateModule(Protocol):
    def check(self, f: Field, value: str | None, ex: RawExtraction,
              context: dict[str, str | None]) -> tuple[FailureKind, str]:
        """(실패 유형, 사유) 를 반환한다. 통과면 FailureKind.NONE."""


class VerifyAgentModule(Protocol):
    def verify(self, path: str, f: Field, value: str | None,
               ex: RawExtraction) -> tuple[bool, str]:
        """bbox 크롭 역방향 검증. (확인됨, 상세) 를 반환한다."""


# ══════════════════════════════════════════════════════════════
#  기본 구현 — 모듈이 없을 때 자리를 지킨다
# ══════════════════════════════════════════════════════════════

class DefaultTriage:
    """데이터가 사전 정비되었다는 전제. 미지원 포맷만 걸러낸다.

    담당: 강민호 책임 (src/triage) — 3-class 판정으로 교체
    """

    def run(self, path: str) -> TriageResult:
        ext = os.path.splitext(path)[1].lower()
        stats = {"ext": ext, "size": _size(path)}
        if ext not in SUPPORTED:
            return TriageResult(path, DocumentClass.UNSUPPORTED, [], 1.0,
                                f"미지원 포맷 {ext}", stats)
        return TriageResult(path, DocumentClass.DATASHEET, [Target()], 0.5,
                            "사전 정비된 코퍼스로 가정 (기본 구현)", stats)


class DefaultRouter:
    """확장자 기반. PDF 는 텍스트 레이어를 확인하지 못하므로 VLM 으로 보낸다.

    담당: 강민호 책임 (src/router) — 텍스트 레이어 탐침으로 교체
    ※ 교체하지 않으면 텍스트 PDF 30% 를 VLM 으로 보내 비용·환각 위험이 늘어난다
    """

    def run(self, triage: TriageResult) -> tuple[ParserType, str, dict[str, Any]]:
        ext = os.path.splitext(triage.source_path)[1].lower()
        if ext in EXCEL_EXT:
            return ParserType.EXCEL, f"{ext} 확장자", {}
        if ext in IMAGE_EXT:
            return ParserType.VLM, f"{ext} 이미지", {}
        return (ParserType.VLM, "PDF — 텍스트 레이어 미탐침 (기본 구현)",
                {"warn": "probe_text_layer 미구현"})


class NullParser:
    """아무것도 추출하지 못한다. 모든 필드가 N/A 경로를 타므로
    비고·승인 차단·상태 판정을 지금 검증할 수 있다.

    담당: 서경빈 선임 (src/parsers/text) · 강민호 책임 (src/parsers/vlm)
    """

    def extract(self, path, triage, fields) -> list[RawExtraction]:
        return [RawExtraction(field_key=f.key, raw_value=None,
                              note="파서 미구현 (기본 구현)") for f in fields]

    def reread(self, path, f, prev, attempt=1) -> RawExtraction | None:
        return None


class DefaultNormalize:
    """schema/rules.yaml 의 도메인 규칙을 적용한다. 실제로 동작하는 구현.

    ATO → Fail Close 같은 역전 매핑이 여기서 처리되고 transform_trace 가 쌓인다.
    담당: 이종수 책임 (src/normalize) — 규칙 데이터는 서경빈 선임
    """

    def run(self, ex: RawExtraction, f: Field) -> tuple[str | None, list[str]]:
        if not ex.found:
            return None, []
        raw = str(ex.raw_value).strip()
        trace = [f"원문 {raw!r}"]

        rule = schema.domain_rule(f.key)
        if rule:
            probe = schema.norm_label(raw)
            # ① 값 자체가 ATO/ATC 인 경우 — 역전 매핑
            for m in rule.get("map", []):
                for cand in m.get("from", []):
                    if schema.norm_label(cand) == probe or probe.startswith(schema.norm_label(cand)):
                        trace.append(m.get("trace", f"규칙 {cand} → {m['to']}"))
                        trace.append(f"결과 {m['to']}")
                        return m["to"], trace

            # ② 라벨이 직접 기재인 경우 — 값에는 방향만 적혀 있다
            #    "Air Fails Valve to : Close" → FAIL CLOSE
            #    값만 보면 역전 표기와 구분이 불가능하므로 **라벨을 본다**.
            #    라벨을 **문자열 목록으로 맞추지 않는다.** 실측에서
            #    `Air fail Valve to` · `Air failure valve to` 가 목록의
            #    `AIR FAILS VALVE TO` 와 한 글자씩 달라 전부 빗나갔다.
            #    목록을 늘리면 변종이 끝없이 나온다(인사이트 50) — 대신
            #    규칙의 원래 의도인 **"FAIL 어간이 있으면 직접 기재"** 를 쓴다.
            got = schema.norm_label(ex.raw_label or "")
            direct = bool(got) and "FAIL" in got
            #    단, `Fail/Air-To` 처럼 직접·역전 표기가 한 라벨에 같이 있으면
            #    방향을 정할 수 없다. **안전 필드이므로 변환하지 않고 넘긴다.**
            if direct and re.search(r"AIRTO|ATO\b|ATC\b", got):
                trace.append(f"원문라벨 {ex.raw_label!r} 에 직접·역전 표기가 "
                             f"함께 있어 방향을 정할 수 없다 — 사람 확인")
                direct = False
            if direct:
                for m in rule.get("direct_map") or []:
                    if probe in {schema.norm_label(c) for c in m.get("from", [])}:
                        trace.append(f"원문라벨 {ex.raw_label!r} 은 직접 기재")
                        trace.append(m.get("trace", f"{raw} → {m['to']}"))
                        trace.append(f"결과 {m['to']}")
                        return m["to"], trace

            # ③ 라벨도 값도 못 가리면 변환하지 않는다.
            #    역전 여부를 모르는 채 방향을 정하면 안전 사양을 뒤집는다.
            trace.append("규칙 미적용 — 표기가 사전에 없고 라벨로도 방향을 알 수 없음")

        for m in schema.value_aliases(f.key):
            if schema.norm_label(raw) in {schema.norm_label(c) for c in m.get("from", [])}:
                trace.append(f"표기 정규화 → {m['to']}")
                return m["to"], trace

        if not schema.feature_enabled("unit_conversion"):
            trace.append("단위 변환 비활성 (MVP) — 원문 보존")
        return raw, trace


class DefaultFormatValidate:
    """필수 충족만 확인한다.

    담당: 서경빈 선임 (src/validate/format) — 타입·형식 검사 추가
    """

    def check(self, f, value, ex, context) -> tuple[FailureKind, str]:
        if value is None or str(value).strip() == "":
            if not ex.found:
                return FailureKind.NO_EVIDENCE, "문서에서 값을 찾지 못함"
            return FailureKind.EXTRACTION, "값이 비어 있음"
        return FailureKind.NONE, ""


class DefaultDomainValidate:
    """원문 표기 방식과 결과값의 정합성을 검산한다.

    Fail Action 은 양식마다 두 가지 표기가 있고 결과가 정반대다.

        원문에 "Fails" 가 있으면  →  [직접] 표기  →  원문과 결과가 같아야 함
                                                    "Air Fails to: Close" → Fail Close
        원문에 "Fails" 가 없으면  →  [역전] 표기  →  원문과 결과가 반대여야 함
                                                    "Air-to-Open (ATO)"  → Fail Close

    규칙이 반대로 적용되면 이 검산이 무조건 잡는다. 다른 필드가 필요 없어
    raw_value 와 value 만으로 판정되므로 MVP 에서 바로 돌아간다.

    ※ ACTUATOR TYPE 과의 교차검증은 넣지 않았다. 컨트롤밸브는 대부분 공기식이라
      거의 발동하지 않고, 스프링 복귀형 전기 액추에이터라는 정당한 예외가 있어
      오탐이 안전 필드를 리뷰필요로 밀어내기만 한다.

    담당: 이종수 책임 (src/validate/domain) — 유효 ANSI 클래스·배관 규격 추가
    """

    def check(self, f, value, ex, context) -> tuple[FailureKind, str]:
        if f.key != "actuator_fail_action" or not value or not ex.found:
            return FailureKind.NONE, ""

        raw = schema.norm_label(ex.raw_value)      # 대소문자·공백·구두점 제거
        out = schema.norm_label(value)
        raw_open, raw_close = "OPEN" in raw, "CLOSE" in raw
        out_open, out_close = "OPEN" in out, "CLOSE" in out

        # 원문·결과 어느 쪽에서든 방향을 못 읽으면 판정하지 않는다 (FAIL LAST 등)
        if not (raw_open ^ raw_close) or not (out_open ^ out_close):
            return FailureKind.NONE, ""

        direct = "FAIL" in raw          # Fails / Fail 이 있으면 직접 기재
        same = (raw_open and out_open) or (raw_close and out_close)

        if direct and not same:
            return (FailureKind.CONSTRAINT,
                    f"원문 {ex.raw_value!r} 은 직접 기재(Fails 포함)이므로 결과가 같아야 하는데 "
                    f"{value} 임 — 역전 규칙이 잘못 적용된 것으로 보임")
        if not direct and same:
            return (FailureKind.CONSTRAINT,
                    f"원문 {ex.raw_value!r} 은 역전 표기(Fails 없음)이므로 결과가 반대여야 하는데 "
                    f"{value} 임 — 직접 규칙이 잘못 적용된 것으로 보임")
        return FailureKind.NONE, ""


class NullVerifyAgent:
    """평가 Agent 미구현 — 확인하지 못했으므로 사람에게 넘긴다.

    미구현 검증기가 True 를 반환하면 낮은 확신도 값이 조용히 자동확정된다.
    안전 기본값은 '확인 못 함' 이다.

    담당: 이종수 책임 (src/validate/domain) — bbox 크롭 역방향 검증
    """

    def verify(self, path, f, value, ex) -> tuple[bool, str]:
        return False, "평가 Agent 미구현 — 확신도 미달 건은 사람 확인"


# ══════════════════════════════════════════════════════════════
#  하네스
# ══════════════════════════════════════════════════════════════

class Pipeline:
    def __init__(
        self,
        triage: TriageModule | None = None,
        router: RouterModule | None = None,
        text_parser: ParserModule | None = None,
        vlm_parser: ParserModule | None = None,
        normalizer: NormalizeModule | None = None,
        format_validator: ValidateModule | None = None,
        domain_validator: ValidateModule | None = None,
        verify_agent: VerifyAgentModule | None = None,
        *,
        max_retries: int = 2,
        only_mvp: bool = True,
        use_vlm: bool = True,
    ) -> None:
        self.triage = triage or DefaultTriage()
        self.router = router or DefaultRouter()
        self.text_parser = text_parser or NullParser()
        self.vlm_parser = vlm_parser or NullParser()
        self.normalizer = normalizer or DefaultNormalize()
        self.format_validator = format_validator or DefaultFormatValidate()
        self.domain_validator = domain_validator or DefaultDomainValidate()
        self.verify_agent = verify_agent or NullVerifyAgent()
        self.max_retries = max(0, int(max_retries))   # 0 = Loop A 비활성
        self.only_mvp = only_mvp
        self.use_vlm = use_vlm                        # False = 베이스라인 ②

    # ── 필드 목록 ─────────────────────────────────────────
    def target_fields(self) -> tuple[Field, ...]:
        return schema.mvp_fields() if self.only_mvp else schema.all_fields()

    # ── Loop B · 문서 배치 ────────────────────────────────
    def run_batch(self, paths: Sequence[str], run_id: str | None = None,
                  echo: bool = False) -> list[DocumentResult]:
        run_id = run_id or time.strftime("%Y%m%d-%H%M%S")
        out_dir = hooks.start_run(
            run_id, schema.config_hashes(),
            {"fields": len(self.target_fields()), "only_mvp": self.only_mvp,
             "use_vlm": self.use_vlm, "max_retries": self.max_retries,
             "docs": len(paths), "models": models.summary()},
            echo=echo,
        )
        results = []
        for p in paths:
            results.append(self.run_document(p))

        ok = [r for r in results if not r.error]
        agg: dict[str, int] = {}
        for r in ok:
            for k, v in r.counts().items():
                agg[k] = agg.get(k, 0) + v
        total = sum(agg.values()) or 1
        summary = hooks.end_run({
            "docs_total": len(results),
            "docs_ok": len(ok),
            "docs_failed": len(results) - len(ok),
            "fields": agg,
            "auto_rate": round(agg.get("auto", 0) / total, 4),
            "approvable": sum(1 for r in ok if r.approvable),
        })
        summary["out_dir"] = out_dir
        self.last_summary = summary
        return results

    # ── 문서 1건 ──────────────────────────────────────────
    def run_document(self, path: str) -> DocumentResult:
        doc_id = _doc_id(path)
        t0 = time.perf_counter()

        try:
            with hooks.stage("triage", doc_id):
                tri = self.triage.run(path)
        except Exception as e:
            return DocumentResult(doc_id, path, _fail_triage(path, e), [], 0, str(e))

        if not tri.processable:
            hooks.on_escalate(doc_id, "-", tri.reason or "처리 대상 아님")
            return DocumentResult(doc_id, path, tri, [],
                                  int((time.perf_counter() - t0) * 1000),
                                  error=f"{tri.document_class.value}: {tri.reason}")

        try:
            with hooks.stage("route", doc_id):
                ptype, reason, stats = self.router.run(tri)
                if ptype is ParserType.VLM and not self.use_vlm:
                    ptype, reason = ParserType.PDF_TEXT, "VLM 비활성(베이스라인) — 텍스트 경로로 강제"
                hooks.on_route_decided(doc_id, ptype, reason, stats)

            parser = self.vlm_parser if ptype is ParserType.VLM else self.text_parser
            fields = self.target_fields()

            with hooks.stage("extract", doc_id):
                extractions = parser.extract(path, tri, fields)
                by_key = {e.field_key: e for e in extractions}
                for f in fields:
                    by_key.setdefault(f.key, RawExtraction(
                        field_key=f.key, raw_value=None, parser=ptype,
                        note="파서가 반환하지 않음"))
                for e in by_key.values():
                    if e.parser != ptype:
                        e = replace(e, parser=ptype)
                        by_key[e.field_key] = e
                    hooks.on_field_extracted(doc_id, by_key[e.field_key])

            with hooks.stage("resolve", doc_id):
                records = self._resolve_fields(doc_id, path, parser, fields, by_key)

        except Exception as e:
            return DocumentResult(doc_id, path, tri, [],
                                  int((time.perf_counter() - t0) * 1000), str(e))

        return DocumentResult(doc_id, path, tri, records,
                              int((time.perf_counter() - t0) * 1000))

    # ── Loop A · 필드 재시도 ──────────────────────────────
    def _resolve_fields(self, doc_id: str, path: str, parser: ParserModule,
                        fields: Sequence[Field],
                        by_key: dict[str, RawExtraction]) -> list[FieldRecord]:
        context: dict[str, str | None] = {}
        records: dict[str, FieldRecord] = {}

        # 1차 처리
        for f in fields:
            rec = self._process(doc_id, path, f, by_key[f.key], context, attempt=0)
            records[f.key] = rec
            context[f.key] = rec.value

        # 재시도 — 추출 실패에만. 제약 위반은 사람에게 넘긴다.
        for attempt in range(1, self.max_retries + 1):
            targets = [f for f in fields
                       if records[f.key].failure is FailureKind.EXTRACTION]
            if not targets:
                break
            for f in targets:
                prev = by_key[f.key]
                fresh = None
                try:
                    fresh = parser.reread(path, f, prev, attempt)
                except TypeError:
                    # attempt 를 받지 않는 구형 파서 — 계약 확장 이전 구현
                    try:
                        fresh = parser.reread(path, f, prev)
                    except Exception as e:
                        hooks.on_error(doc_id, f"reread:{f.key}", e)
                except Exception as e:
                    hooks.on_error(doc_id, f"reread:{f.key}", e)
                if fresh is None:
                    # 재판독 미지원 — 더 돌려도 같으므로 즉시 확정
                    records[f.key] = replace(
                        records[f.key], retry_count=attempt,
                        note=_join(records[f.key].note, "재판독 미지원으로 재시도 중단"))
                    continue
                hooks.on_retry(doc_id, f.key, attempt,
                               prev.raw_value, fresh.raw_value,
                               records[f.key].note)
                by_key[f.key] = fresh
                rec = self._process(doc_id, path, f, fresh, context, attempt=attempt)
                rec.retry_values = [str(prev.raw_value), str(fresh.raw_value)]
                records[f.key] = rec
                context[f.key] = rec.value

        # 확정 및 기록
        out = []
        for f in fields:
            rec = records[f.key]
            try:
                rec.validate()
            except ValueError as e:
                # 계약 위반 — 조용히 넘기지 않고 비고에 남긴다
                rec.note = _join(rec.note, f"[계약위반] {e}")
                hooks.on_error(doc_id, f"contract:{f.key}", e)
            hooks.on_state_resolved(doc_id, rec)
            if rec.state is not FieldState.AUTO:
                hooks.on_escalate(doc_id, f.key, rec.note)
            out.append(rec)
        return out

    # ── 필드 1개 처리 ─────────────────────────────────────
    def _process(self, doc_id: str, path: str, f: Field, ex: RawExtraction,
                 context: dict[str, str | None], attempt: int) -> FieldRecord:
        t0 = time.perf_counter()

        value, trace = self.normalizer.run(ex, f)
        if trace and value != ex.raw_value:
            hooks.on_transform(doc_id, f.key, ex.raw_value, value,
                               rule="domain_rules", trace=trace)

        failure, detail = self.format_validator.check(f, value, ex, context)
        if failure is FailureKind.NONE:
            failure, detail = self.domain_validator.check(f, value, ex, context)
        if failure is not FailureKind.NONE:
            hooks.on_violation(doc_id, f.key, failure, detail)

        # 평가 Agent — 검증 통과 + 확신도 미달인 경우에만 (비용 계단)
        # 확인되면 확신도 임계를 통과한 것으로 승격한다. 그것이 이 단계의 목적이다.
        agent_ok: bool | None = None
        agent_note = ""
        if failure is FailureKind.NONE and ex.confidence < f.threshold:
            try:
                agent_ok, note_ = self.verify_agent.verify(path, f, value, ex)
            except Exception as e:
                hooks.on_error(doc_id, f"verify:{f.key}", e)
                agent_ok, note_ = False, f"평가 Agent 오류: {e}"
            hooks.on_agent_verdict(doc_id, f.key, agent_ok, note_)
            if not agent_ok:
                agent_note = _join("평가 Agent 불일치", note_)

        state, note = _decide(f, ex, value, failure, detail, agent_note, agent_ok)

        return FieldRecord(
            doc_id=doc_id, field_key=f.key, field_name=f.name,
            value=value, raw_value=ex.raw_value, raw_label=ex.raw_label,
            state=state, failure=failure, note=note,
            confidence=ex.confidence, threshold=f.threshold,
            bbox=ex.bbox, page=ex.page, source_locator=ex.source_locator,
            transform_trace=trace, required=f.required, safety=f.safety,
            parser=ex.parser, retry_count=attempt,
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
        )


# ══════════════════════════════════════════════════════════════
#  상태 판정 — 임계값과 안전등급은 fields.yaml 에서 온다
# ══════════════════════════════════════════════════════════════

def _decide(f: Field, ex: RawExtraction, value: str | None,
            failure: FailureKind, detail: str,
            agent_note: str, agent_ok: bool | None = None) -> tuple[FieldState, str]:
    if failure is FailureKind.NO_EVIDENCE:
        return FieldState.NA, _join(detail or "문서에 근거 없음", ex.note)
    if failure is FailureKind.CONSTRAINT:
        return FieldState.REVIEW, _join("제약 위반 — 재시도하지 않고 확인 필요", detail)
    if failure is FailureKind.FORMAT:
        return FieldState.REVIEW, _join("형식·허용값 위반", detail)
    if failure is FailureKind.EXTRACTION:
        return FieldState.REVIEW, _join("추출 실패", detail, ex.note)
    if agent_ok is False:
        return FieldState.REVIEW, agent_note or "평가 Agent 불일치"
    if agent_ok is True:
        # 확신도는 낮았으나 역방향 검증이 확인함 → 자동확정으로 승격
        return FieldState.AUTO, ""
    if ex.confidence < f.threshold:
        return FieldState.REVIEW, (
            f"확신도 {ex.confidence:.2f} < 임계 {f.threshold:.2f}")
    return FieldState.AUTO, ""


# ══════════════════════════════════════════════════════════════
#  유틸
# ══════════════════════════════════════════════════════════════

def _join(*parts: str) -> str:
    return " | ".join(p.strip() for p in parts if p and str(p).strip())


def _doc_id(path: str) -> str:
    base = os.path.splitext(os.path.basename(path))[0]
    return re.sub(r"\s+", "_", base)[:80] or "doc"


def _size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _fail_triage(path: str, e: Exception) -> TriageResult:
    return TriageResult(path, DocumentClass.UNSUPPORTED, [], 0.0, f"Triage 오류: {e}")


# ══════════════════════════════════════════════════════════════
#  자체 확인 — 모듈 없이 전 구간이 도는지
# ══════════════════════════════════════════════════════════════

class _SmokeParser:
    """합성 데이터로 모든 경로를 한 번씩 통과시킨다."""

    SAMPLE = {
        "engineering_tag_no":   ("10-FV-001",         "TAG NO.",        0.99),
        "manufacturer":         ("FISHER",            "MANUFACTURER",   0.97),
        "model_no":             ("667-EZ",            "MODEL",          0.93),
        "valve_body_size":      ('2"',                "SIZE",           0.95),
        "valve_body_rating":    ("ANSI CLASS 300",    "RATING",         0.88),
        "valve_body_material":  ("WCB",               "BODY MATL",      0.72),
        "actuator_fail_action": ("Air-to-Open (ATO)", "FAIL POSITION",  0.91),
        "rated_cv_normal":      (None,                None,             0.00),
    }

    def extract(self, path, triage, fields):
        out = []
        for f in fields:
            v, lab, conf = self.SAMPLE.get(f.key, (None, None, 0.0))
            out.append(RawExtraction(
                field_key=f.key, raw_value=v, raw_label=lab, confidence=conf,
                bbox=(0.34, 0.12, 0.64, 0.17), page=1,
                source_locator="Sheet1!C7" if v else "",
                note="" if v else "문서에 해당 항목 없음"))
        return out

    def reread(self, path, f, prev):
        return None


def _smoke(echo: bool) -> int:
    import json
    sys.stdout.reconfigure(encoding="utf-8")
    print("=" * 68)
    print("  하네스 자체 확인 — 모듈 없이 전 구간")
    print("=" * 68)
    print(json.dumps(schema.summary(), ensure_ascii=False, indent=2))

    p = Pipeline(text_parser=_SmokeParser(), vlm_parser=_SmokeParser())
    res = p.run_batch(["fixtures/_smoke/19-FV-001.xlsx"], run_id="_smoke", echo=echo)
    r = res[0]

    print(f"\n문서: {r.doc_id}   경로: {r.triage.document_class.value}   "
          f"{r.elapsed_ms}ms   승인가능: {r.approvable}")
    print(f"{'필드':<24}{'상태':<8}{'값':<20}{'확신':<7}비고")
    print("-" * 100)
    for rec in r.records:
        mark = "*" if rec.safety != "normal" else " "
        print(f"{mark}{rec.field_key:<23}{rec.state.value:<8}"
              f"{str(rec.value)[:19]:<20}{rec.confidence:<7.2f}{rec.note[:44]}")
    print("-" * 100)
    print("* = 안전·식별 필드 (자동확정이어도 사람 확인 표시)")
    print(f"\n상태 집계: {r.counts()}")
    print(f"산출물: {p.last_summary['out_dir']}")

    # 검증
    ok = True
    fa = next(x for x in r.records if x.field_key == "actuator_fail_action")
    if fa.value != "FAIL CLOSE":
        print(f"\n[실패] ATO → FAIL CLOSE 변환 안 됨: {fa.value}"); ok = False
    else:
        print(f"\n[확인] 도메인 규칙 — {fa.raw_value} → {fa.value}")
        for t in fa.transform_trace:
            print(f"        {t}")
    cv = next(x for x in r.records if x.field_key == "rated_cv_normal")
    if cv.state is not FieldState.NA:
        print(f"[실패] 근거 없는 필드가 N/A 가 아님: {cv.state}"); ok = False
    else:
        print(f"[확인] N/A 경로 — {cv.note}")
    if r.approvable:
        print("[실패] 미해소 필드가 있는데 승인 가능으로 나옴"); ok = False
    else:
        print("[확인] 승인 차단 — 미해소 필수 필드 존재")
    if all(x.note for x in r.records if x.state is not FieldState.AUTO):
        print("[확인] 자동확정 외 상태 전부 비고 보유")
    else:
        print("[실패] 비고 없는 비자동확정 레코드 존재"); ok = False

    print("\n" + ("전 구간 정상" if ok else "위 [실패] 항목 확인 필요"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="D2S 파이프라인")
    ap.add_argument("paths", nargs="*", help="처리할 파일")
    ap.add_argument("--smoke", action="store_true", help="모듈 없이 자체 확인")
    ap.add_argument("--all-fields", action="store_true", help="MVP 8필드 대신 전체 30필드")
    ap.add_argument("--no-vlm", action="store_true", help="베이스라인 ② — VLM 제외")
    ap.add_argument("--max-retries", type=int, default=2, help="Loop A 상한 (0=비활성)")
    ap.add_argument("--echo", action="store_true", help="Hook 이벤트를 콘솔에 출력")
    a = ap.parse_args()

    if a.smoke or not a.paths:
        return _smoke(a.echo)

    sys.stdout.reconfigure(encoding="utf-8")
    p = Pipeline(only_mvp=not a.all_fields, use_vlm=not a.no_vlm,
                 max_retries=a.max_retries)
    res = p.run_batch(a.paths, echo=a.echo)
    s = p.last_summary
    print(f"문서 {s['docs_ok']}/{s['docs_total']} 처리   "
          f"자동확정률 {s['auto_rate']:.1%}   승인가능 {s['approvable']}건")
    print(f"필드 상태: {s['fields']}")
    print(f"산출물: {s['out_dir']}")
    for r in res:
        if r.error:
            print(f"  [실패] {r.doc_id}: {r.error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
