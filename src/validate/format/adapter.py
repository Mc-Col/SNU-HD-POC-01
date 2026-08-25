# -*- coding: utf-8 -*-
"""⑤-a 형식 검증을 파이프라인에 꽂는 어댑터.

■ 무슨 작업인가
────────────────────────────────────────────────────────────────────
`src/pipeline.py` 는 `format_validator` 를 생성자로 주입받고, 기본값
`DefaultFormatValidate` 는 **값이 비었는지만** 본다(그 클래스 주석에
"담당: 서경빈 선임 — 타입·형식 검사 추가" 라고 적혀 있다).

이 어댑터를 넣으면 `schema/rules.yaml` 의 `format_rules` 가 실제로 돌아간다.

    from src.pipeline import Pipeline
    from src.validate.format import FormatValidator
    Pipeline(format_validator=FormatValidator())

파이프라인 파일은 소유자만 고친다(CLAUDE.md 철학 1). 주입 지점이 이미 있으므로
여기서 계약만 맞춘다 — 텍스트 파서 어댑터(`src/parsers/text/adapter.py`)와 같은 방식이다.

■ 판정 규칙
────────────────────────────────────────────────────────────────────
값이 없을 때는 기본 구현과 같게 판정한다 — 문서에서 못 찾았으면 `NO_EVIDENCE`,
찾았는데 비었으면 `EXTRACTION`. **형식 위반은 재시도 대상이 아니므로**
`FailureKind.FORMAT` 으로만 표시하고 사유를 남긴다(철학 5).

위반이 여러 개면 사유를 모두 잇는다. 사람이 화면에서 한 번에 보는 것이 낫다.
"""
from __future__ import annotations

from src.contracts import FailureKind

from .validator import check_value, load_fields, load_format_rules


class FormatValidator:
    """계약 `ValidateModule.check` 를 구현한다."""

    def __init__(self, fields: list[dict] | None = None,
                 rules: dict[str, dict] | None = None) -> None:
        # 파일을 문서마다 다시 읽지 않는다 (같은 입력 → 같은 출력, 철학 6)
        self.fields = fields if fields is not None else load_fields()
        self.rules = rules if rules is not None else load_format_rules()

    def check(self, f, value, ex, context) -> tuple[FailureKind, str]:
        if value is None or str(value).strip() == "":
            if not getattr(ex, "found", False):
                return FailureKind.NO_EVIDENCE, "문서에서 값을 찾지 못함"
            return FailureKind.EXTRACTION, "값이 비어 있음"

        bad = [v for v in check_value(f.key, value, self.fields, self.rules)
               if v.rule != "required"]        # 값이 있으므로 required 는 해당 없음
        if not bad:
            return FailureKind.NONE, ""
        return FailureKind.FORMAT, " · ".join(v.as_note() for v in bad)
