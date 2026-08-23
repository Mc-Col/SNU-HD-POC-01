# -*- coding: utf-8 -*-
"""
Hook 버스 — 이 파일은 이종수 책임만 수정한다.

각 Hook 에는 KPI 나 안전 속성이 하나씩 걸려 있다. 모듈 개발자는 자기 모듈에만
집중하고, 로깅·측정·감사 추적은 하네스가 처리한다.

사용법 (모듈 코드에서):

    from src.hooks import hooks

    hooks.on_field_extracted(doc_id, extraction)
    hooks.on_unmapped_label(doc_id, "FAIL POSITION", "Sheet1!B12")

단계 진입·종료는 컨텍스트 매니저로 자동 처리된다 — 잊을 수가 없다:

    with hooks.stage("triage", doc_id):
        result = triage.run(path)

산출물:
    runs/<run_id>/events.jsonl    모든 Hook 이벤트
    runs/<run_id>/records.jsonl   확정된 FieldRecord
    runs/<run_id>/summary.json    KPI 집계

원칙: Hook 은 절대 예외를 던지지 않는다. 로깅 실패가 파이프라인을 죽이면 안 된다.
"""
from __future__ import annotations

import io
import json
import os
import time
import traceback
from collections import Counter
from contextlib import contextmanager
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS_DIR = os.path.join(ROOT, "runs")


def _jsonable(v: Any) -> Any:
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if hasattr(v, "value"):          # Enum
        return v.value
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    return str(v)


class HookBus:
    """실행 1회당 하나. pipeline 이 start_run / end_run 을 호출한다."""

    def __init__(self) -> None:
        self.run_id: str | None = None
        self._dir: str | None = None
        self._events: io.TextIOWrapper | None = None
        self._records: io.TextIOWrapper | None = None
        self._t0: float = 0.0
        self.counters: Counter[str] = Counter()
        self.echo: bool = False          # True 면 콘솔에도 출력 (개발 중)

    # ── 실행 수명 ──────────────────────────────────────────
    def start_run(self, run_id: str, config_hashes: dict[str, str] | None = None,
                  meta: dict[str, Any] | None = None, echo: bool = False) -> str:
        self.run_id = run_id
        self.echo = echo
        self._dir = os.path.join(RUNS_DIR, run_id)
        os.makedirs(self._dir, exist_ok=True)
        self._events = open(os.path.join(self._dir, "events.jsonl"), "a", encoding="utf-8")
        self._records = open(os.path.join(self._dir, "records.jsonl"), "a", encoding="utf-8")
        self._t0 = time.time()
        self.counters.clear()
        self.on_config_load(config_hashes or {}, meta or {})
        return self._dir

    def end_run(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        summary = {
            "run_id": self.run_id,
            "elapsed_sec": round(time.time() - self._t0, 2),
            "counters": dict(sorted(self.counters.items())),
            **(extra or {}),
        }
        if self._dir:
            with open(os.path.join(self._dir, "summary.json"), "w", encoding="utf-8") as f:
                json.dump(_jsonable(summary), f, ensure_ascii=False, indent=2)
        for fh in (self._events, self._records):
            try:
                fh and fh.close()
            except Exception:
                pass
        self._events = self._records = None
        return summary

    # ── 내부 기록 ──────────────────────────────────────────
    def _emit(self, event: str, doc_id: str | None = None, **kw: Any) -> None:
        self.counters[event] += 1
        row = {"ts": round(time.time() - self._t0, 4), "event": event, "doc_id": doc_id}
        row.update(_jsonable(kw))
        line = json.dumps(row, ensure_ascii=False)
        try:
            if self._events:
                self._events.write(line + "\n")
                self._events.flush()
            elif self.echo:
                print("[hook]", line)
            if self.echo and self._events:
                print("[hook]", line)
        except Exception:
            pass   # 로깅 실패가 파이프라인을 죽이지 않는다

    # ══════════════════════════════════════════════════════
    #  Hook 13개
    # ══════════════════════════════════════════════════════

    def on_config_load(self, hashes: dict[str, str], meta: dict[str, Any]) -> None:
        """재현성 — 어느 규칙으로 낸 결과인지 모르면 개선을 측정할 수 없다."""
        self._emit("on_config_load", None, hashes=hashes, meta=meta)

    @contextmanager
    def stage(self, name: str, doc_id: str | None = None):
        """단계 진입·종료·오류를 자동 기록. 개발자가 잊을 수 없는 구조."""
        t = time.perf_counter()
        self._emit("on_stage_enter", doc_id, stage=name)
        try:
            yield
        except Exception as e:
            self.on_error(doc_id, name, e)
            raise
        finally:
            ms = int((time.perf_counter() - t) * 1000)
            self._emit("on_stage_exit", doc_id, stage=name, elapsed_ms=ms)

    def on_route_decided(self, doc_id: str, parser: Any, reason: str = "",
                         stats: dict[str, Any] | None = None) -> None:
        """경로별 정확도 분해의 근거. 스캔 경로가 유의하게 나쁜지 판단한다."""
        self._emit("on_route_decided", doc_id, parser=parser, reason=reason, stats=stats or {})

    def on_field_extracted(self, doc_id: str, extraction: Any) -> None:
        """raw_label · bbox · 확신도 기록. 유사표현 사전의 원천이자 재시도 비교 기준선."""
        self._emit(
            "on_field_extracted", doc_id,
            field=getattr(extraction, "field_key", None),
            raw_value=getattr(extraction, "raw_value", None),
            raw_label=getattr(extraction, "raw_label", None),
            confidence=getattr(extraction, "confidence", None),
            bbox=getattr(extraction, "bbox", None),
            parser=getattr(extraction, "parser", None),
            locator=getattr(extraction, "source_locator", ""),
        )

    def on_unmapped_label(self, doc_id: str, label: str, locator: str = "") -> None:
        """사전에 없는 표기 수집 → schema/rules.yaml 보강 후보. Loop C 의 입력."""
        self._emit("on_unmapped_label", doc_id, label=label, locator=locator)

    def on_transform(self, doc_id: str, field_key: str, before: Any, after: Any,
                     rule: str = "", trace: list[str] | None = None) -> None:
        """transform_trace 생성. 원문 병기 UI 의 원천 — 없으면 검토자가 값만 본다."""
        self._emit("on_transform", doc_id, field=field_key, before=before,
                   after=after, rule=rule, trace=trace or [])

    def on_retry(self, doc_id: str, field_key: str, attempt: int,
                 prev_value: Any, new_value: Any, reason: str = "") -> None:
        """1·2차 값 비교. 근거 없는 값 변경 = 환각 신호."""
        changed = str(prev_value) != str(new_value)
        self._emit("on_retry", doc_id, field=field_key, attempt=attempt,
                   prev_value=prev_value, new_value=new_value,
                   changed=changed, reason=reason)
        if changed:
            self.counters["retry_value_changed"] += 1

    def on_violation(self, doc_id: str, field_key: str, kind: Any, detail: str = "") -> None:
        """위반 유형 기록. 규칙이 과민한지 실제 오류인지 사후 분류한다."""
        self._emit("on_violation", doc_id, field=field_key, kind=kind, detail=detail)

    def on_agent_verdict(self, doc_id: str, field_key: str, confirmed: bool,
                         detail: str = "") -> None:
        """평가 Agent 의 역방향 검증 결과."""
        self._emit("on_agent_verdict", doc_id, field=field_key,
                   confirmed=confirmed, detail=detail)

    def on_state_resolved(self, doc_id: str, record: Any) -> None:
        """자동확정률 집계. record.validate() 가 비고 누락을 예외로 잡는다."""
        state = getattr(getattr(record, "state", None), "value", None)
        self.counters[f"state:{state}"] += 1
        self._emit("on_state_resolved", doc_id,
                   field=getattr(record, "field_key", None), state=state,
                   confidence=getattr(record, "confidence", None),
                   note=getattr(record, "note", ""),
                   retry_count=getattr(record, "retry_count", 0))
        try:
            if self._records:
                self._records.write(json.dumps(
                    _jsonable(record.to_row()), ensure_ascii=False) + "\n")
                self._records.flush()
        except Exception:
            pass

    def on_escalate(self, doc_id: str, field_key: str, reason: str) -> None:
        """사유 필수. 없으면 검토자가 판단할 근거가 없다."""
        if not str(reason).strip():
            reason = "(사유 미기재 — 계약 위반)"
            self.counters["escalate_without_reason"] += 1
        self._emit("on_escalate", doc_id, field=field_key, reason=reason)

    def on_human_action(self, doc_id: str, field_key: str, action: str,
                        before: Any = None, after: Any = None,
                        by: str = "", elapsed_ms: int = 0) -> None:
        """사람의 판단 기록. 후속 Contextual Bandit 의 학습 데이터."""
        self._emit("on_human_action", doc_id, field=field_key, action=action,
                   before=before, after=after, by=by, elapsed_ms=elapsed_ms)

    def on_rule_edit(self, field_key: str, before: Any, after: Any,
                     by: str = "", source: str = "guidance") -> None:
        """Loop C — 사람이 판단 기준을 글로 남긴 순간. 규칙표 개선의 원재료다."""
        self._emit("on_rule_edit", None, field=field_key, source=source,
                   before=before, after=after, by=by)

    def on_error(self, doc_id: str | None, stage: str, exc: BaseException) -> None:
        """실패를 삼키지 않는다. 처리 실패율도 측정 대상이다."""
        self._emit("on_error", doc_id, stage=stage,
                   error=type(exc).__name__, message=str(exc)[:400],
                   trace=traceback.format_exc(limit=4)[-800:])


# 모듈 코드가 그냥 import 해서 쓰는 싱글턴
hooks = HookBus()


if __name__ == "__main__":
    import sys, tempfile
    sys.stdout.reconfigure(encoding="utf-8")
    hooks.start_run("_selftest", {"schema/fields.yaml": "abc123"}, {"note": "self test"})
    with hooks.stage("triage", "d1"):
        hooks.on_route_decided("d1", "excel", "xlsx 확장자")
    hooks.on_unmapped_label("d1", "FAIL POSITION", "Sheet1!B12")
    hooks.on_retry("d1", "rated_cv_normal", 1, "83.1", "83.1", "저해상도")
    hooks.on_retry("d1", "rated_cv_normal", 2, "83.1", "88.4", "재판독")
    hooks.on_escalate("d1", "trim_material", "")
    try:
        with hooks.stage("parse", "d1"):
            raise ValueError("파서 미구현")
    except ValueError:
        pass
    s = hooks.end_run({"docs": 1})
    print(json.dumps(s, ensure_ascii=False, indent=2))
    print("\n주목:")
    print("  retry_value_changed       =", s["counters"].get("retry_value_changed"),
          "← 근거 없이 값이 바뀐 횟수 (환각 신호)")
    print("  escalate_without_reason   =", s["counters"].get("escalate_without_reason"),
          "← 사유 없는 에스컬레이션 (계약 위반)")
