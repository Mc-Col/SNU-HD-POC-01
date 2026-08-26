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
from dataclasses import dataclass, field

from src import schema
from src.contracts import (
    DocumentClass, DocumentResult, FailureKind, FieldRecord, FieldState,
    ParserType, RawExtraction, Target, TriageResult,
)
from src.validate import domain

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DIR = os.path.join(ROOT, "fixtures", "ui")
FIXTURE_JSON = os.path.join(FIXTURE_DIR, "sample_document_result.json")
FIXTURE_PDF = os.path.join(FIXTURE_DIR, "sample_page.pdf")
FIXTURE_MULTI = os.path.join(FIXTURE_DIR, "sample_multipage.pdf")


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
    origin: str                      # "fixture" | "pipeline" | "vlm"
    # 이 지면 이미지가 **원본의 몇 쪽인가.** 스캔은 한 쪽만 떠 오므로
    # 이미지 자체에는 쪽 번호가 없다. 이것이 없으면 bbox 를 어느 쪽 것과
    # 맞춰야 할지 알 수 없어, 다른 쪽 박스를 그리거나 전부 사라진다.
    page_no: int = 1
    # 표시원 계산에 필요한 원재료. 계약에 넣지 않는 이유는 아래 flags() 참고
    raws: dict[str, RawExtraction] = field(default_factory=dict)

    def flags(self, rec: FieldRecord) -> list[domain.Flag]:
        """이 칸에 붙는 확인필요 표시 전부 — **매번 다시 계산한다.**

        저장하지 않는 이유: 표시는 (값 · 추출 · 문맥 · 규칙)의 파생물이고,
        저장하면 규칙이 바뀔 때 낡은 채로 남는다. 조합을 아는 곳은
        `src/validate/domain.check_all` 하나뿐이다 — 화면도 평가 하네스도
        같은 함수를 부르므로 "화면에서 본 것과 채점된 숫자" 가 갈리지 않는다.
        """
        try:
            f = schema.get(rec.field_key)
        except KeyError:
            return []
        return domain.check_all(f, rec.final_value, self.raws.get(rec.field_key),
                                self.raws or None, confidence=rec.confidence)

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


def from_fixture(path: str | None = None, page_path: str | None = None,
                 page: int = 1) -> UiDoc:
    """`page_path` 를 주면 그 지면 위에 bbox 를 그린다.

    다중 페이지 픽스처(`sample_multipage.pdf`)의 1쪽이 이 사양표와 같은
    지면이므로, 쪽 고르기 화면을 지나온 흐름에서도 좌표가 그대로 맞는다.
    """
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

    sheet = page_path if (page_path and os.path.exists(page_path)) else FIXTURE_PDF
    if os.path.splitext(sheet)[1].lower() != ".pdf":
        # tif 는 PDF 뷰어로 못 띄운다 — 실제 경로와 같게 PNG 로 떠서 넘긴다
        sheet = render_page_png(sheet, page) or FIXTURE_PDF
    result = DocumentResult(
        doc_id=doc_id, source_path=sheet, triage=tri,
        records=records, elapsed_ms=int(fx.get("elapsed_ms", 0)),
    )
    return UiDoc(
        result=result,
        display_name=fx.get("source_name", os.path.basename(FIXTURE_PDF)),
        page_path=sheet,
        size_bytes=int(fx.get("source_bytes", 0)),
        route_reason=fx.get("route", {}).get("reason", ""),
        origin="fixture",
        page_no=page,
    )


# ── 실제 파이프라인 ───────────────────────────────────────────

class _PickedPageTriage:
    """사람이 고른 쪽을 Triage 결과로 심는다.

    `Pipeline.run_document(path)` 는 쪽 인자를 받지 않고 Triage 에게 묻는다.
    Triage 가 쪽을 모르면 파서가 1쪽을 읽는다 — 쪽 지정 오류는 실측에서 값을
    가장 크게 바꾼 결함이었다(홀드아웃 d040 46% → 93%, `docs/eval_history.md`).
    화면은 사람이 고른 쪽을 알고 있으므로 여기서 주입한다.
    """

    def __init__(self, page: int) -> None:
        self.page = max(1, int(page or 1))

    def run(self, path: str):
        from src.contracts import (DocumentClass, PageClass, PageInfo, Target,
                                   TriageResult)
        from src.preprocess import SUPPORTED, caution_reason, parse_filename

        ext = os.path.splitext(path)[1].lower()
        stats = {"ext": ext, "picked_page": self.page}
        if ext not in SUPPORTED:
            return TriageResult(path, DocumentClass.UNSUPPORTED, [], 1.0,
                                f"미지원 포맷 {ext}", stats)
        info = parse_filename(path)
        reason = f"사람이 p{self.page} 를 지정"
        warn = caution_reason(path)
        return TriageResult(
            source_path=path, document_class=DocumentClass.DATASHEET,
            targets=[Target(page_from=self.page, page_to=self.page)],
            confidence=0.5, reason=f"{reason} / {warn}" if warn else reason,
            stats=stats, file_tag=info.tag_raw or "",
            pages=[PageInfo(page=self.page, page_class=PageClass.SPEC,
                            selected=True, reason="사람이 고름")],
        )


def from_pipeline(path: str, *, only_mvp: bool = True, use_vlm: bool = True,
                  page: int | None = None) -> UiDoc:
    """완성된 모듈을 꽂은 파이프라인으로 처리한다.

    배선은 `src.pipeline.build()` 한 곳에만 있다 — 화면과 CLI 가 다른 결과를
    내지 않게 하려는 것이다(2026-08-25 조립).

    꽂히지 않은 모듈이 있으면 `build()` 가 사유를 돌려준다. 그것을 화면의
    판정 근거에 함께 실어, 값이 왜 안 나오는지 사람이 알 수 있게 한다.

    `page` 를 주면 그 쪽을 처리한다 — 주지 않으면 Triage 의 판단을 쓴다."""
    from src.pipeline import DefaultNormalize, build

    notes: list[str] = []
    pg = int(page or 1)
    p = build(only_mvp=only_mvp, use_vlm=use_vlm, notes=notes)
    if page:
        p.triage = _PickedPageTriage(pg)
    # 화면의 두 경로가 **같은 정규화**를 쓰게 맞춘다 — 채점된 구성이 이것이다.
    # `Normalizer` 는 안전 필드에서 라벨을 오판할 수 있다(2026-08-27 확인:
    # 값 `Open` + 라벨 `Air to Actuator` 를 FAIL OPEN 으로 바꾼다. 정답은
    # FAIL CLOSE — 그 라벨은 역전 표기다). 틀린 값을 만드는 쪽보다 원문을
    # 남겨 사람에게 보내는 쪽이 안전하다.
    p.normalizer = DefaultNormalize()
    result = p.run_document(path)
    ext = os.path.splitext(path)[1].lower()
    return UiDoc(
        result=result,
        display_name=os.path.basename(path),
        # tif 는 PDF 뷰어로 못 띄운다 — PNG 로 떠서 넘긴다(대상의 71.9%)
        page_path=(path if ext == ".pdf" else render_page_png(path, pg)),
        size_bytes=os.path.getsize(path) if os.path.exists(path) else 0,
        route_reason=" | ".join([result.triage.reason,
                                 *(f"[조립] {n}" for n in notes)]),
        origin="pipeline",
        page_no=pg,
    )


# ── VLM 경로 ──────────────────────────────────────────────────
#
#  실제 문서를 읽는 유일한 경로다. 대상의 71.9% 가 스캔 tif 이므로 화면에서
#  의미 있는 것을 보려면 여기를 거쳐야 한다.
#
#  평가 하네스(`eval/harness.py --stage vlm`)와 **같은 부품**을 쓴다 —
#  화면에서 본 것과 채점된 숫자가 다르면 둘 중 하나는 거짓이다.

def render_page_png(path: str, page: int = 1) -> str | None:
    """페이지를 PNG 로 떠서 경로를 돌려준다. 화면 표시·bbox 오버레이용.

    tif 는 PDF 뷰어로 못 띄우므로 반드시 이 변환이 필요하다.
    실패하면 None — 화면은 이미지 없이 표 만이라도 보여준다(철학 5).
    """
    import tempfile

    from src import preprocess
    try:
        out = os.path.join(tempfile.gettempdir(), "d2s_ui_pages")
        os.makedirs(out, exist_ok=True)
        got = preprocess.render_pages(path, out, pages=[int(page or 1)])
        return got[0] if got else None
    except Exception:
        return None


def from_vlm(path: str, *, only_mvp: bool = False,
             page: int | None = None) -> UiDoc:
    """실제 문서를 VLM 으로 읽어 화면용 결과를 만든다.

    기본은 **전체 28필드**다 (2026-08-25 결정 — 발표 헤드라인이 28필드다).
    슬라이드는 28인데 시연이 9면 나머지 19개를 묻게 된다.

    page 를 주지 않으면 1페이지를 읽는다. 사양표 페이지 자동 선택은 Triage
    구현 후에 붙는다 — 지금은 사람이 화면에서 페이지를 고른다.

    ④ Normalize 까지 적용한다. 파서는 문서 원문(`Close`)을 내고 표준값
    (`FAIL CLOSE`)은 Normalize 몫이므로, 화면에는 최종값과 원문을 함께 보여준다.
    """
    from src import schema
    from src.contracts import (DocumentClass, DocumentResult, FailureKind,
                               FieldRecord, PageClass, PageInfo, TriageResult)
    from src.pipeline import DefaultNormalize, _decide, build

    from src import preprocess

    pg = int(page or 1)
    info = preprocess.parse_filename(path)
    triage = TriageResult(
        source_path=path, document_class=DocumentClass.DATASHEET,
        file_tag=info.tag_raw or "",
        pages=[PageInfo(page=pg, page_class=PageClass.SPEC, selected=True)],
        reason=preprocess.caution_reason(path) or "VLM 판독",
    )

    fields = [f for f in (schema.mvp_fields() if only_mvp else schema.all_fields())
              if f.source == "document"]
    from src.validate import domain

    # 부품은 `build()` 에서 꺼낸다 — **배선 지식을 화면이 갖지 않는다.**
    # 손으로 조립하면 DualParser 는 얻어도 VLM 응답 캐시와 실제 Normalizer 를
    # 잃는다. 캐시가 없으면 같은 문서를 두 번 읽을 때 값이 달라져(실측: 합의율
    # 71% ↔ 100%) 철학 6 이 깨지고, 시연에서 두 번 돌리면 다른 화면이 나온다.
    #
    # DualParser 로 두 경로가 함께 돈다 — VLM 이 필드 배정의 주인이고 텍스트는
    # 글자를 보증한다. tif 는 TEXT_EXT 밖이라 텍스트 경로가 비고, 그때는
    # 대조 없이 VLM 단독과 같아진다. 그 사실이 route_reason 에 그대로 남는다.
    notes: list[str] = []
    built = build(only_mvp=only_mvp, use_vlm=True, notes=notes)
    parser = built.vlm_parser
    # 정규화는 **채점된 구성과 같은 것**을 쓴다 — 평가 하네스가 DefaultNormalize
    # 다(eval/harness.py). `Normalizer` 는 값이 빈 필드를 태그에서 도출해 채우고,
    # `type_name` 정답이 30건 중 29건 NA 인 코퍼스에서 그것은 지어냄이 된다
    # (830칸 중 12 → 41건). 도출을 켜는 것은 정책 결정이고 T8 로 보류됐다.
    norm = DefaultNormalize()
    raws = parser.extract(path, triage, fields)
    context = {e.field_key: e for e in raws}
    doc_id = os.path.basename(path)

    records = []
    for ex in raws:
        f = schema.get(ex.field_key)
        value, trace = norm.run(ex, f)

        # 표시원은 조립 함수 하나에서만 나온다. 확신도는 `_decide` 가 직접
        # 보므로 여기서 또 넣지 않는다 — 넣으면 사유 문구가 어긋난다.
        hard = [fl for fl in domain.check_all(f, value, ex, context,
                                              confidence=ex.confidence)
                if fl.source != "확신도"]
        if not value:
            # 값이 없으면 NO_EVIDENCE 로 넘겨야 N/A 로 판정된다 —
            # 파이프라인과 같은 판정 함수를 쓴다. 화면과 채점이 갈리면 안 된다.
            failure, detail = FailureKind.NO_EVIDENCE, ""
        elif hard:
            failure, detail = hard[0].kind, " / ".join(fl.why for fl in hard)
            for fl in hard:
                if fl.source == "어휘":
                    # 후보 큐에 쌓아 두면 사전 승인 화면이 한 번에 보여준다
                    domain.observe_all(f, value, doc_id, label=ex.raw_label or "")
        else:
            failure, detail = FailureKind.NONE, ""
        state, note = _decide(f, ex, value, failure, detail, "", None)
        records.append(FieldRecord(
            doc_id=os.path.basename(path), field_key=f.key, field_name=f.name,
            value=value, raw_value=ex.raw_value, raw_label=ex.raw_label,
            state=state, failure=failure, note=note,
            confidence=ex.confidence, threshold=f.threshold,
            bbox=ex.bbox, page=ex.page, source_locator=ex.source_locator,
            transform_trace=trace, required=f.required, safety=f.safety,
            parser=ex.parser,
        ))

    result = DocumentResult(
        doc_id=doc_id, source_path=path, triage=triage, records=records)
    return UiDoc(
        result=result,
        display_name=os.path.basename(path),
        page_path=render_page_png(path, pg),
        size_bytes=os.path.getsize(path) if os.path.exists(path) else 0,
        route_reason=_join_reason(triage.reason,
                                  getattr(parser, "last_summary", None), notes),
        origin="vlm",
        page_no=pg,
        raws=context,
    )


def _join_reason(reason: str, summary: dict[str, int] | None,
                 notes: list[str] | None = None) -> str:
    """두 경로 대조 결과와 조립 관찰을 화면에 한 줄로 남긴다.

    "두 경로가 독립으로 읽어 대조한다" 를 말하려면 그 숫자가 화면에 있어야 한다.
    0 인 항목은 적지 않는다 — tif 는 텍스트 경로가 비어 전부 `VLM만` 으로 나온다.
    조립에 실패한 모듈이 있으면 그 사유도 함께 — 값이 왜 안 나오는지 화면에서 보이게.
    """
    parts = [reason]
    if summary:
        got = " · ".join(f"{k} {v}" for k, v in summary.items() if v)
        if got:
            parts.append(f"텍스트 대조 — {got}")
    parts += [f"[조립] {n}" for n in (notes or [])]
    return " / ".join(p for p in parts if p)


def ensure_fixture_page(multi: bool = False) -> str:
    """합성 지면이 없으면 만든다. 픽스처에서 생성되므로 bbox 와 항상 일치한다."""
    want = FIXTURE_MULTI if multi else FIXTURE_PDF
    if not os.path.exists(want):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_make_sample", os.path.join(FIXTURE_DIR, "make_sample.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.build()
        mod.build_multipage()
    return want
