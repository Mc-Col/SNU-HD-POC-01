# -*- coding: utf-8 -*-
"""③-b VLM PARSER — 스캔 페이지에서 값·위치·확신도를 읽는다

    from src.parsers.vlm.openai_vlm import VlmParser

    p = VlmParser()
    recs = p.extract(path, triage, fields)          # 1차 — luna
    fresh = p.reread(path, field, prev, attempt=1)   # 재판독 — terra, bbox 크롭만

왜 이 경로가 주 경로인가
─────────────────────────────────────────────────────────────
대상 1,021건 중 `tif` 가 **71.9%**(734건)이고 전부 스캔이다. 텍스트 레이어가
없으므로 이 파서 없이는 코퍼스의 3/4 에 손을 댈 수 없다.

첫 실측 (2026-08-24, gpt-5.6-luna, 골든셋 d001 p1)
    이미지 1240x1753 · 입력 2,619토큰 · 출력 144토큰
    `Size and Type` → `2" 667-EZ`   정확
    `Tag`           → `10-ETV-002`  **오독** (정답 10-FV-002)

Tag 옆의 수기 체크(√)를 글자로 읽었다. 그런데 이 오독은 **규칙으로 잡힌다** —
파일명이 `A10FV002` 를 주고 읽은 값은 `A10ETV002` 로 정규화되어 불일치가
자동 검출된다. 식별 필드에 사람 확인을 강제하는 이유가 이것이다.

설계
─────────────────────────────────────────────────────────────
① 값과 함께 **원문 항목명(`raw_label`)** 을 요구한다. 유사표현 사전이
   여기서 자란다 — 28필드의 표기 변종은 실물에서 수집되어야 한다.
② **bbox 를 요구한다.** 화면 하이라이트의 근거이고, 재판독 크롭의 좌표다.
   규약은 정규화 0.0~1.0, 좌상단 원점, 페이지별 독립(`src/contracts.py`).
③ 문서에 없으면 `null` 을 내라고 명시한다. 만들어내는 것이 이 과제가
   없애려는 문제다.
④ 재판독은 **bbox 크롭만** 보낸다. 페이지 전체를 다시 돌리지 않으므로
   실질 해상도가 올라가고 비용은 필드 하나분이다.
⑤ 재시도 프롬프트에 "값이 틀렸다" 고 쓰지 않는다 — 환각을 유도한다.
   문구는 *"이 영역에 문자 그대로 무엇이 적혀 있는지 보고하라"* 다.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import tempfile
from typing import Any, Sequence

from src import models, preprocess, schema
from src.contracts import ParserType, RawExtraction, TriageResult

# 이미지 토큰이 입력의 대부분이다. 필드 정의는 문서마다 같으므로 캐시된다.
MAX_OUT = 8000


def _b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def _field_spec(fields: Sequence) -> str:
    """필드 정의를 프롬프트에 넣을 형태로. 문서마다 동일하므로 캐시 대상이다."""
    lines = []
    for f in fields:
        al = " · ".join(f.aliases) if f.aliases else ""
        line = f"- {f.key} | {f.name}"
        if al:
            line += f" | 문서 표기 예: {al}"
        if f.desc:
            line += f" | {f.desc}"
        lines.append(line)
    return "\n".join(lines)


def _maker_table() -> str:
    """모델명 → 제조사. 제조사가 안 적힌 문서가 많아 함께 넣는다.

    **포지셔너 규칙은 뺀다.** 포지셔너는 문서가 대개 모델번호만 주므로
    모델명 판단이 보조가 아니라 주경로가 되고, 그러면 결과가 전부 문서에
    없는 값이 된다(2026-08-25 실측 14건). 본체 제조사는 문서가 대개
    말해 주므로 이 표가 예외 경로로만 돈다 — 그쪽은 유지한다.
    """
    rules = [r for r in schema.manufacturer_rules()
             if r.get("kind") != "positioner"]
    if not rules:
        return ""
    out = ["", "■ 모델명으로 제조사를 아는 표 (문서에 제조사가 없을 때만 쓴다)"]
    for r in rules:
        out.append(f"- {' / '.join(r.get('prefix') or [])} → {r.get('to')}"
                   f" ({r.get('kind', '')})")
    out.append("이 표로 채운 값은 raw_label 에 \"(모델명으로 판단)\" 을 적어라.")
    return "\n".join(out)


# 없음 사유 → note 접두. 계약의 FailureKind 가 NO_EVIDENCE 와 EXTRACTION 을 구분하고
# 하류 처리가 다르다 — 근거 없음은 N/A 로 확정, 판독 실패는 재시도 대상이다.
# 사유를 note 로만 구분하지 않으면 하류가 둘을 가릴 수 없다.
NOTE_BY_ABSENCE = {
    "no_evidence": "근거 없음 — 문서에 항목 자체가 없음",
    "unreadable": "판독 실패 — 항목은 있으나 값을 읽을 수 없음",
    "checkbox_ambiguous": "판독 실패 — 체크박스 표시가 없거나 둘 이상이거나 판독 불가",
}


def _note_for(value, d: dict) -> str:
    """note 를 조립한다 — 없음 사유, 모델 관찰, 행 전체 텍스트를 함께 남긴다.

    입력  : value — 정리된 raw_value(None 가능), d — 모델 응답 항목
    출력  : note 문자열
    부수효과: 없음
    """
    parts = []
    if value is None:                              # 값이 없으면 왜 없는지가 핵심 정보다
        reason = str(d.get("absence_reason") or "").strip()
        parts.append(NOTE_BY_ABSENCE.get(reason, "문서에서 찾지 못함"))
    row = str(d.get("row_text") or "").strip()      # 한 칸이 여러 필드를 먹인 경우의 원본
    if row:
        parts.append(f"행 원문: {row}")
    model_note = str(d.get("note") or "").strip()   # 모델이 남긴 관찰 (체크박스 상태 등)
    if model_note:
        parts.append(model_note)
    return " | ".join(parts)


def upscale_factor() -> float:
    """`D2S_UPSCALE` 배율. 1 이면 확대하지 않는다.

    tif 는 1비트 Group 4 라 DPI 를 올려도 원본 해상도가 안 늘어난다. 확대는
    **보간으로 획을 키우는 것**이고 명암 개선과는 다른 수단이다. 손글씨 숫자
    오독이 최대 실패군이므로 표적이 맞는다.
    """
    try:
        v = float(os.getenv("D2S_UPSCALE", "1"))
    except ValueError:
        return 1.0
    return v if 1.0 < v <= 4.0 else 1.0


def target_long_edge() -> int:
    """`D2S_LONG_EDGE` 목표 장변(px). 0 · 미설정이면 끈다.

    왜 배율(`D2S_UPSCALE`) 로는 부족한가 (2026-08-26 실측)
    ─────────────────────────────────────────────────────────
    배율은 **원본 크기에 종속**된다. 우리 코퍼스의 렌더 장변은 이렇게 갈린다.

        tif  748건 중 99.6%  1240x1753          장변 1753
        pdf  181건 1,120페이지                   장변 1500 ~ **7306**
             (최대는 A0 도면을 150dpi 로 렌더한 5168x7306)

    여기에 1.47 을 일괄로 주면 tif 는 2576 이 되지만 A0 도면은 10,740 이 된다.
    포맷마다 글자 해상도가 달라져 A/B 가 성립하지 않고, 큰 쪽은 이미지 토큰이
    44,518 까지 올라 비용과 모델 상한에 걸린다.

    목표 장변을 주면 **원본과 무관하게 결과가 일정**해지고, 큰 문서는 줄어든다.

    우선순위 — `D2S_UPSCALE` 과 둘 다 설정되면 **이쪽이 이긴다.** 더 구체적인
    지정이기 때문이다. 미설정이면 기존 배율 경로가 그대로 돈다(하위 호환).

    범위 — 512 ~ 8192 밖은 무시하고 0(끄기)으로 본다. 오타로 극단값이 들어가
    비용이 폭증하는 것을 막는다.
    """
    try:
        v = int(os.getenv("D2S_LONG_EDGE", "0"))
    except ValueError:
        return 0                                        # 숫자가 아니면 끈 것으로 본다
    return v if 512 <= v <= 8192 else 0


def crop_zoom() -> float:
    """재판독 크롭에 추가로 줄 배율. `D2S_CROP_ZOOM`, **기본 1(끄기)**.

    글자 오독(자릿수는 같고 한두 글자 다름)을 표적으로 2배를 시험했으나
    **효과를 증명하지 못했다.** 개발셋 30건에서 글자 오독이 10 → 13 → 11 로
    기준선보다 오히려 많았다. 게다가 그 실행에만 `--escalate` 를 함께 켜서
    규칙 16 · 크롭 확대 · 에스컬레이션 셋이 섞였다 — **측정 설계 실수다.**

    스위치는 남긴다. 순수 비교(에스컬레이션만 켜고 배율 없이 / 배율까지)를
    할 기회가 있으면 그때 다시 잰다.
    """
    try:
        v = float(os.getenv("D2S_CROP_ZOOM", "1"))
    except ValueError:
        return 1.0
    return v if 1.0 <= v <= 6.0 else 1.0


def upscale_cap() -> int:
    """확대 후 장변 상한(px). `D2S_UPSCALE_CAP`, 기본 5000. 0 이면 무제한.

    배율(`D2S_UPSCALE`)은 **원본 크기에 종속**된다. 코퍼스 렌더 장변이
    1500~7306px 로 갈리므로(A0 도면을 150dpi 로 렌더한 5168x7306) 2배를 일괄로
    주면 14,612px 이 되고 이미지 토큰이 44,000 을 넘어 비용과 모델 상한에 걸린다.

    **상한만 건다.** 골든셋+홀드아웃 35건의 2배 확대 후 최대 장변은 4678px 이라
    기본값 5000 은 **측정 대상 전부를 손대지 않는다.** 대형 도면만 잘린다.

    장변 목표(`D2S_LONG_EDGE`)로 바꾸지 않은 이유 — 2026-08-27 실측에서
    홀드아웃이 반대로 나왔다(관대 87% → 81%, 세 문서가 각각 −3칸, 뭉침·방향·기전
    모두 있음). **작아진 문서가 나빠진다.** 확대는 유지하고 상한만 건다.
    """
    try:
        v = int(os.getenv("D2S_UPSCALE_CAP", "5000"))
    except ValueError:
        return 5000
    return v if v == 0 or 1024 <= v <= 16384 else 5000


def prep_steps() -> tuple[str, ...]:
    """`D2S_PREP` 에 적힌 이미지 조정 단계. 쉼표로 구분한다."""
    v = os.getenv("D2S_PREP", "")
    return tuple(x.strip() for x in v.split(",") if x.strip())


def _conditioned(png: str) -> str:
    """모델에 보낼 이미지로 손질한다. 구현은 `src/imageprep` 에 있다.

    순서가 중요하다 — **구멍 제거를 먼저, 확대를 나중에** 한다. 확대 후에
    지우면 같은 일을 4배 넓이에서 하게 되고, 보간으로 번진 테두리 때문에
    원형 판정도 흐려진다.
    """
    from src import imageprep
    out = png
    if "holes" in prep_steps():
        out, _n = imageprep.remove_punch_holes(out)
    # 크기 조정은 두 갈래다. 장변 목표가 있으면 그쪽이 이긴다 — 더 구체적인
    # 지정이고, 배율과 달리 **축소도 하므로** 대형 도면을 줄일 수 있다.
    edge = target_long_edge()
    if edge:
        out = imageprep.resize_long_edge(out, edge)     # 확대·축소 양방향
    else:
        k = upscale_factor()                            # 기존 경로 — 확대 전용
        if k > 1.0:
            out = imageprep.upscale(out, k)
            # 상한 — 대형 도면만 자른다. 우리 측정 대상(최대 4678px)은 안 걸린다.
            cap = upscale_cap()
            if cap:
                from PIL import Image as _Image
                with _Image.open(out) as _im:
                    if max(_im.size) > cap:
                        out = imageprep.resize_long_edge(out, cap)
    return out


SYSTEM = """너는 컨트롤밸브 데이터시트를 읽는 판독기다. 추론하지 말고 판독한다.

규칙
1. 문서에 **적혀 있는 글자 그대로** 옮긴다. 단위·대소문자·기호를 바꾸지 않는다.
2. 문서에 없으면 raw_value 를 null 로 둔다. **절대 만들어내지 않는다.**
   빈칸·해당없음·미기재는 모두 null 이다.
2-1. null 일 때는 **왜 없는지**를 absence_reason 에 적는다. 하류 처리가 다르다.
   - "no_evidence" : 문서에 그 항목 자체가 없다 (칸도 라벨도 없다)
   - "unreadable"  : 항목·라벨은 보이지만 값의 글씨를 읽을 수 없다 (흐림·겹침·잘림)
   - "checkbox_ambiguous" : 체크박스인데 표시가 없거나 둘 이상이거나 판독 불가
   값이 있으면 absence_reason 은 "present" 다.
2-2. **한 칸의 값이 여러 필드에 걸쳐 있으면 그 칸 전체를 row_text 에 그대로 담는다.**
   예) 라벨 "Size and Type" 의 값이 '2", 667-EZ' 이면 바디 사이즈와 모델번호가 한 칸에 있다.
   각 필드의 raw_value 에는 해당 조각을 담되, row_text 에는 '2", 667-EZ' 전체를 담는다.
   하류에 칸 전체를 분해하는 규칙이 있어서, 원본이 남아 있어야 그 규칙이 동작한다.
3. raw_label 에는 그 값 옆에 적힌 **항목명을 그대로** 적는다. 항목명이 없고
   로고·머리글에만 있는 값이면 위치를 괄호로 적는다 — 예: (좌측 상단 로고)
4. bbox 는 그 값이 있는 위치다. [x0, y0, x1, y1], 각 0.0~1.0, 좌상단이 (0,0).
5. confidence 는 판독 확신도다. 글씨가 흐리거나 체크박스가 애매하면 낮춘다.
   추측해서 채운 값에 높은 확신도를 주지 않는다.
6. 체크박스 양식 주의 — ☒ 표시가 어느 칸에 있는지 확인한다. 손으로 그린
   체크도 있다. 어느 칸인지 확실하지 않으면 confidence 를 낮춘다.
7. **Min / Nor(Normal) / Max 세 열이 나란한 표가 많다. 반드시 Normal 열을
   읽는다.** 열 제목이 Minimum·Normal·Maximum 이거나 MIN.·NOR.·MAX. 로
   줄여 있다. 열이 하나뿐이면 그것을 쓴다. 어느 열인지 모르면 confidence 를
   낮추고 raw_label 에 "(열 불명)" 을 적는다.
   같은 행에서 값을 셋 다 읽어 가운데를 고르는 것이 아니라, **열 제목을 보고**
   Normal 에 해당하는 칸을 고른다.
7-1. **`Normal` 이라는 단어가 없는 표도 있다.** 벤더마다 운전 조건 열을 다르게
   부른다 — `Cond 1|Cond 2|…`, `Case 1|Case 2|…`, `Operating`, `Rated`.
   이때는 **맨 왼쪽 운전 조건 열**을 읽는다. 값을 비우지 마라.
   그리고 **어느 열에서 읽었는지 raw_label 끝에 괄호로 적는다** — 예:
   `Temperature (Cond 1)`. 사람이 화면에서 그 한 줄로 검증한다.
   ⚠ `Design`·`Shutoff`·`Max Allowable` 은 운전 조건이 아니다. 설계 한계이므로
   운전 조건 열이 따로 있으면 그쪽을 쓴다.
8. 값이 있는 행을 착각하지 않도록, 반드시 **그 값의 왼쪽 항목명을 함께 읽어
   raw_label 에 적는다.** 항목명과 값이 어긋나면 그 판독은 틀린 것이다.
9. **한 필드에는 값 하나만. 괄호·화살표·부가기호가 남으면 틀린 것이다.**
   - 괄호 안 부가정보는 버린다:  195 (Cg=4040 -> 7580)  =>  195
   - 개조 전후가 함께 적히면 화살표 뒤(개조 후)만 쓴다
       4" X 2-7/8" -> 4" X 4"   =>   4"
       치수가 곱해져 있으면 본체 호칭(앞의 큰 값)을 쓴다
   - 규격 접미는 뺀다:  1" (25A) => 1" ,  NPS 3/4 => 3/4"
   - 슬래시 나열은 Normal 열 하나만:  12.051 / 12.249  =>  앞의 것
   버린 정보는 raw_label 끝에 괄호로 적어 근거를 남긴다.
   자기 점검 — raw_value 에 ( 나 -> 가 남아 있으면 다시 고른다.
10. **태그가 여러 개 적힌 페이지가 있다.** 같은 사양의 밸브를 한꺼번에
   개조하면 10-FV-011 / 012 / 013 / 014 처럼 한 장으로 갈음하기 때문이다.
   engineering_tag_no 에는 **아래에 주어진 이 파일의 태그**만 넣는다.
   페이지의 태그 목록 전체를 넣지 않는다.
11. **제조사(manufacturer)는 밸브를 만든 회사다. 시공·정비 업체가 아니다.**
   개조·정비 문서는 하단 꼬리말·주소 블록에 **시공사** 이름이 찍혀 있다.
   그것을 제조사로 쓰면 틀린다. 판단 순서:
     (1) 문서에 Maker / Manufacturer 라고 **명시된 칸**이 있으면 그 값.
         **표 밖에 있어도 된다.** 1986년 양식은 이 표기가 표가 아니라
         도면 우하단이나 서명란 근처에 도장처럼 찍혀 있다. 로고보다
         **항상 우선한다** — 서식 발행처와 실제 제작사가 다를 수 있다.
         (실측: 15FV037 · 19XV036 은 FISHER 서식인데 하단에
          `MANUFACTURER : N/MASONEILAN` 이 찍혀 있다)
     (2) 없으면 모델명으로 판단한다 (아래 표)
     (3) Note / 비고 문장에 적혀 있을 수 있다 —
         예: 현재 Valve의 Body 사용 (Model : ED, FISHER)
     (4) 그래도 모르면 null 이다. **꼬리말 회사명으로 채우지 않는다.**
   raw_label 에 출처를 적어라 — (Maker 항목) / (모델명으로 판단) / (Note 문장)
12. **positioner_manufacturer 는 문서가 명시할 때만 채운다.**
   포지셔너 모델번호만 적혀 있고 제조사가 없으면 **null 이다.**
   모델번호로 제조사를 추측하지 마라 — 그것은 문서에 없는 값이다.
13. **체크박스로만 표시된 값도 값이다.** 오래된 양식은 항목 옆에 선택지를
   늘어놓고 체크 표시만 해 둔다 — `Stem  ☒ Std.` · `Flowing Media ☒ LIQUID`.
   **체크된 선택지의 문구를 그대로 raw_value 에 적는다.** 빈칸이 아니다.
   ⚠ **반드시 그 항목의 행 안에서만 본다.** 위아래 다른 행의 선택지를
   가져오지 마라. 그 행에 체크가 없으면 null 이다 — 옆 행에서 찾지 마라.
   (실측 오류: `actuator_type` 에 포지셔너 행의 `3570` 을,
    `characteristic` 에 Trim Form 행의 `SINGLE` 을 넣었다)
14. **숫자와 단위를 고쳐 쓰지 마라.** 문서에 적힌 자릿수·표기 그대로다.
   - 소수점을 늘리지 않는다:  `5` 를 `5.00000` 으로 쓰지 않는다
   - 앞자리 0 을 빼지 않는다:  `0.9` 를 `.9` 로 쓰지 않는다
   - 단위를 바꿔 쓰지 않는다:  `℃` 를 `deg C` 로, `m3/h` 를 `m³/h` 로
   - **단위가 적혀 있으면 값에 포함한다.** `49 ℃` 이지 `49` 가 아니다
   ⚠ **단위와 항목명은 다르다.** 값 앞뒤의 **항목명을 단위로 착각해 붙이지
   마라.** 그리고 **원래 단위가 없는 값에는 아무것도 붙이지 않는다** —
   Cv(용량계수)·비중·유량계수는 무차원이다.
       옳음  `2.51`  `1.05`  `116`
       틀림  `2.51 Cv`  `1.05 Sp. Gr.`  `Cv:116`
15. **한 항목의 값만 낸다. 옆 칸 값을 이어 붙이지 마라.**
16. **문서 제목·공사명·프로젝트명은 값이 아니다.** 지면 맨 위의 큰 글씨는
   그 문서가 무엇인지를 말할 뿐이고 어떤 필드의 값도 아니다.
   예) `CONTROL VALVE RETROFIT`(공사명) · `CONTROL VALVE SPECIFICATION`(양식명)
   · `Pilot Operated Regulator`(양식 표제) · `Pressure Reducing`(용도 표기).
   **항목명이 있는 칸의 값만 낸다.** 그 항목의 칸이 비어 있으면 값을 만들지 말고
   `absence_reason: "no_evidence"` 로 비운다.
   ⚠ 특히 `equipment_full_description` 은 **설비 자체의 설명**이지 문서 제목이나
   공사 이름이 아니다. 해당 칸이 없으면 비운다.
   `LS AR / LIQUID` 처럼 나오면 두 항목을 합친 것이다 — 각자의 칸에 넣는다.

출력은 JSON 하나다:
{"fields": {"<field_key>": {"raw_value": "...", "raw_label": "...",
            "row_text": null, "bbox": [x0,y0,x1,y1], "confidence": 0.0,
            "absence_reason": "present", "note": ""}}}
값이 없는 필드도 raw_value: null 로 포함한다."""

# ── 최소 프롬프트 (과적합 검증용) ──────────────────────────────
#
# `D2S_PROMPT=minimal` 이면 이것을 쓰고, 덧붙인 안내(제조사표·태그힌트·
# 보고서경고)도 전부 뺀다. 남는 것은 질문과 출력 계약뿐이다.
#
# 이 변형이 있는 이유 — **규칙을 늘리면 언제나 초기 문서의 정확도가 오른다.**
# 그 규칙이 거기서 나왔으니까. 일반화되는지는 별개 질문이고, 그것을 물으려면
# 규칙 없는 판이 있어야 한다.
SYSTEM_EN = """You transcribe control valve datasheets. Transcribe; do not infer.

RULES
1. Copy **exactly what is written** on the document. Do not change units,
   letter case, or symbols.
2. If it is not on the document, set raw_value to null. **Never invent a value.**
   Blank cells, "not applicable", and unfilled fields are all null.
2-1. When null, record **why** in absence_reason. Downstream handling differs.
   - "no_evidence" : the item itself is absent (no cell, no label)
   - "unreadable"  : the item/label is visible but the value cannot be read
                     (blurred, overlapped, cut off)
   - "checkbox_ambiguous" : a checkbox item with no mark, more than one mark,
                            or an unreadable mark
   When a value exists, absence_reason is "present".
2-2. **If one cell holds values belonging to several fields, put that whole cell
   verbatim into row_text.**
   Example: the label "Size and Type" holding '2", 667-EZ' — body size and model
   number share one cell. Put the matching fragment in each field's raw_value,
   and put the entire '2", 667-EZ' into row_text.
   A downstream rule splits whole cells, and it only works if the original survives.
3. Put the **item label written next to that value, verbatim**, into raw_label.
   If the value has no label and appears only in a logo or header, state its
   position in parentheses — e.g. (top-left logo)
4. bbox is where that value sits. [x0, y0, x1, y1], each 0.0-1.0, origin
   top-left (0,0).
5. confidence is how certain the transcription is. Lower it when the writing is
   faint or a checkbox is ambiguous. Never give high confidence to a value you
   filled in by guessing.
6. Beware checkbox forms — verify which cell carries the ☒ mark. Some marks are
   drawn by hand. If you are not certain which cell it is, lower confidence.
7. **Many tables place Min / Nor(Normal) / Max as three adjacent columns. Always
   read the Normal column.** Column headers appear as Minimum·Normal·Maximum or
   abbreviated MIN.·NOR.·MAX. If there is only one column, use it. If you cannot
   tell which column it is, lower confidence and add "(column unknown)" to
   raw_label.
   Do not read all three values in the row and pick the middle one — **read the
   column header** and choose the cell under Normal.
7-1. **Some tables have no column called `Normal`.** Vendors name the operating
   condition column differently — `Cond 1|Cond 2|…`, `Case 1|Case 2|…`,
   `Operating`, `Rated`. In that case read the **leftmost operating condition
   column.** Do not leave the value empty.
   And **state which column you read in parentheses at the end of raw_label** —
   e.g. `Temperature (Cond 1)`. A reviewer verifies it from that one line.
   ⚠ `Design`·`Shutoff`·`Max Allowable` are not operating conditions. They are
   design limits, so use the operating condition column if one exists separately.
8. To avoid mistaking which row a value belongs to, always **read the item label
   to the left of that value and record it in raw_label.** If label and value do
   not line up, that transcription is wrong.
9. **One value per field. If a parenthesis, arrow, or extra symbol remains, it is
   wrong.**
   - Discard parenthetical extras:  195 (Cg=4040 -> 7580)  =>  195
   - When before/after retrofit values are both written, use only what follows
     the arrow (the post-retrofit value)
       4" X 2-7/8" -> 4" X 4"   =>   4"
       When dimensions are multiplied, use the body nominal size (the larger
       leading value)
   - Strip standard suffixes:  1" (25A) => 1" ,  NPS 3/4 => 3/4"
   - For slash-separated lists take only the Normal column:
     12.051 / 12.249  =>  the first one
   Record what you discarded in parentheses at the end of raw_label so the basis
   survives.
   Self-check — if ( or -> remains in raw_value, choose again.
10. **Some pages carry several tags.** When valves of identical specification are
   retrofitted together, one sheet covers them all — e.g.
   10-FV-011 / 012 / 013 / 014.
   Put only **this file's tag, given below**, into engineering_tag_no.
   Do not put the page's whole tag list.
11. **manufacturer is the company that built the valve. Not the contractor or
   service company.**  ※contractor
   Retrofit and repair documents carry a **contractor** name in the bottom footer
   or address block. Using that as the manufacturer is wrong. Decide in this order:
     (1) If the document has a cell **explicitly labelled** Maker / Manufacturer,
         use that value. **It may sit outside the table.** On 1986 forms this mark
         is not in a table but stamped near the drawing's bottom-right or the
         signature block. It **always outranks a logo** — the form's publisher and
         the actual maker can differ.
         (measured: 15FV037 · 19XV036 are on FISHER forms yet carry
          `MANUFACTURER : N/MASONEILAN` stamped at the bottom)
     (2) If absent, judge from the model designation (table below)
     (3) It may appear in a Note or remarks sentence —
         e.g. 현재 Valve의 Body 사용 (Model : ED, FISHER)
     (4) If still unknown it is null. **Do not fill it from the footer company name.**
   Record the source in raw_label — (Maker field) / (judged from model) / (Note)
12. **Fill positioner_manufacturer only when the document states it.**
   If only a positioner model number is written and no manufacturer, it is **null.**
   Do not guess the manufacturer from a model number — that value is not on the
   document.
13. **A value marked only by a checkbox is still a value.** Older forms list the
   options beside the item and mark one — `Stem  ☒ Std.` · `Flowing Media ☒ LIQUID`.
   **Copy the checked option's wording verbatim into raw_value.** It is not blank.
   ⚠ **Look only within that item's own row.** Do not take an option from the rows
   above or below. If that row has no mark, it is null — do not look in a
   neighbouring row.
   (measured errors: `3570` from the positioner row was put into `actuator_type`;
    `SINGLE` from the Trim Form row was put into `characteristic`)
14. **Do not rewrite numbers or units.** Keep the digits and notation as written.
   - Do not add decimal places:  do not write `5` as `5.00000`
   - Do not drop a leading zero:  do not write `0.9` as `.9`
   - Do not substitute units:  not `℃` as `deg C`, not `m3/h` as `m³/h`
   - **If a unit is written, include it in the value.** `49 ℃`, not `49`
   ⚠ **A unit is not an item label.** Do not mistake an item label beside the
   value for a unit and attach it. And **attach nothing to a value that has no
   unit** — Cv (flow coefficient), specific gravity, and flow coefficient are
   dimensionless.
       correct  `2.51`  `1.05`  `116`
       wrong    `2.51 Cv`  `1.05 Sp. Gr.`  `Cv:116`
15. **Report only that one item's value. Do not concatenate a neighbouring cell.**
16. **A document title, project name, or job name is not a value.** The large type
   at the top of the sheet only says what the document is; it is not any field's
   value.
   Examples: `CONTROL VALVE RETROFIT`(job name) ·
   `CONTROL VALVE SPECIFICATION`(form name) ·
   `Pilot Operated Regulator`(form title) · `Pressure Reducing`(service wording).
   **Report only values from cells that have an item label.** If that item's cell
   is empty, do not invent a value — leave it with
   `absence_reason: "no_evidence"`.
   ⚠ In particular `equipment_full_description` is **a description of the
   equipment itself**, not the document title or job name. If the cell is absent,
   leave it empty.
   If it comes out as `LS AR / LIQUID`, two items were merged — put each in its
   own field.

Output is a single JSON object:
{"fields": {"<field_key>": {"raw_value": "...", "raw_label": "...",
            "row_text": null, "bbox": [x0,y0,x1,y1], "confidence": 0.0,
            "absence_reason": "present", "note": ""}}}
Include fields with no value as raw_value: null.
"""


MINIMAL_SYSTEM = """너는 컨트롤밸브 데이터시트를 읽는다.

아래 항목들의 값을 문서에서 찾아 **적혀 있는 그대로** 옮겨라.
문서에 없으면 raw_value 를 null 로 둔다. 만들어내지 마라.

출력은 JSON 하나다:
{"fields": {"<field_key>": {"raw_value": "...", "raw_label": "...",
            "bbox": [x0,y0,x1,y1], "confidence": 0.0}}}
raw_label 은 그 값 옆에 적힌 항목명, bbox 는 위치(0.0~1.0, 좌상단 원점),
confidence 는 판독 확신도다. 값이 없는 필드도 raw_value: null 로 포함한다."""


def minimal_mode() -> bool:
    """최소 프롬프트로 돌고 있나."""
    return os.getenv("D2S_PROMPT") == "minimal"


# 도메인 문맥 한 줄. `D2S_PROMPT=domain` 일 때만 앞에 붙인다.
#
# **한 줄만 바꾼다** — 변수가 하나여야 결과를 해석할 수 있다. 그리고
# 채택 여부는 정확도만으로 정하지 않는다. 배경을 주면 모델이 "이런 문서엔
# 보통 이런 값이 있다" 로 빈칸을 메울 수 있고, 그러면 정확도가 올라도
# `근거없음오답` 이 늘어난다. **그때는 채택하지 않는다.**
DOMAIN_LINE = ("이 문서들은 한국 석유화학 플랜트의 컨트롤밸브 사양서다. "
               "1986년부터 현재까지 여러 벤더의 양식이 섞여 있고, "
               "손으로 쓴 것과 인쇄된 것이 함께 있다.\n\n")


def domain_mode() -> bool:
    return os.getenv("D2S_PROMPT") == "domain"


def english_mode() -> bool:
    """`D2S_PROMPT=en` — 지시문을 영어판으로 바꾼다.

    문서가 전부 영어인데 지시만 한국어다. 이 지시문은 **모델만 읽으므로**
    언어를 모델 기준으로 고를 수 있다(`rules.yaml`·`guidance.yaml` 과 다르다).
    번역은 지시 산문만 바꾸고 문서 문자열·예시·구조는 그대로 둔다 —
    언어 변수만 움직이게 하기 위한 것이다.
    """
    return os.getenv("D2S_PROMPT", "").strip().lower() == "en"


def nobbox_mode() -> bool:
    """bbox 를 요구하지 않고 돌고 있나 — 판독 주의 가설 검증용."""
    return os.getenv("D2S_PROMPT") == "nobbox"


# bbox 를 뺀 출력 계약. 나머지 규칙은 그대로 쓴다 — 변수를 하나로 유지한다.
_BBOX_ASK = """출력은 JSON 하나다:
{"fields": {"<field_key>": {"raw_value": "...", "raw_label": "...",
            "bbox": [x0,y0,x1,y1], "confidence": 0.0}}}
값이 없는 필드도 raw_value: null 로 포함한다."""

_NOBBOX_ASK = """출력은 JSON 하나다:
{"fields": {"<field_key>": {"raw_value": "...", "raw_label": "...",
            "confidence": 0.0}}}
값이 없는 필드도 raw_value: null 로 포함한다.
**위치(bbox)는 요구하지 않는다.** 값을 정확히 읽는 데만 집중하라."""


REREAD_SYSTEM = """너는 문서의 한 영역을 판독한다.

이 영역에 **문자 그대로 무엇이 적혀 있는지** 보고하라. 이전 판독과 같은
값이면 같다고 답하라. 다르게 답해야 할 이유는 없다.

규칙
1. **왼쪽 항목명과 값이 같은 행에 있는지 확인하라.** 이 그림은 표의 몇 행을
   잘라낸 것이고, 왼쪽에 항목명이 함께 들어 있다. 찾는 항목명을 먼저 찾고
   **그 행의** 값을 읽어라. 행이 어긋나면 그 판독은 틀린 것이다.
2. 찾는 항목명이 보이지 않으면 raw_value 를 null 로 두고 confidence 를 낮춰라.
   추측해서 옆 행 값을 내지 마라.
3. **값 하나만** 낸다. 괄호 안 부가정보·화살표 뒤 개조표기·슬래시 나열은
   버리고 하나를 고른다.
4. Min / Nor / Max 열이 보이면 Normal 열을 읽어라. 열 제목이 잘려 보이지
   않으면 confidence 를 낮춰라.
5. raw_label 에는 실제로 보인 항목명을 그대로 적어라.

출력은 JSON 하나다:
{"raw_value": "...", "raw_label": "...", "confidence": 0.0}
읽을 수 없으면 raw_value 를 null 로 둔다."""


class VlmParser:
    """계약 `ParserModule` 구현. OpenAI 호환 API 를 쓴다."""

    def __init__(self, client=None, render_dir: str | None = None,
                 only_mvp: bool = False, deskew: bool = False, cache=None):
        """
        deskew — 기하 전처리(방향·기울기 보정)를 켠다. **기본 꺼짐.**
            공용 `src/preprocess.render_pages` 는 페이지를 렌더하지만 기하 보정은
            하지 않는다. 실측(스캔 758건)에서 **71.5% 가 기울기 0.5도를 초과**하고,
            보정하면 글줄 투영 점수가 중앙값 159% 개선된다.
            켜면 모델에 보내는 이미지가 달라지므로 기본값은 꺼둔다 — 검증되지 않은
            동작을 기본으로 만들지 않는다. bbox 는 보정 후에도 원본 기준으로 되돌린다.
        cache — 응답 캐시(`ResponseCache`). **기본 없음(캐시 미사용).**
            VLM API 는 완전한 결정론이 아니므로, 캐시가 없으면 파이프라인 재실행이
            재현되지 않는다(철학 6). 재현성이 필요하면 명시적으로 넘긴다.
        """
        self._client = client
        self.render_dir = render_dir or os.path.join(tempfile.gettempdir(), "d2s_vlm")
        self.only_mvp = only_mvp
        self.deskew = deskew                       # 기하 전처리 사용 여부
        self.cache = cache                         # 응답 캐시 (None 이면 미사용)
        os.makedirs(self.render_dir, exist_ok=True)
        self.calls: list[dict[str, Any]] = []      # 비용 추적 — 로그에 남긴다
        self._transforms: dict[str, Any] = {}      # 렌더 PNG 경로 → PageTransform
        self.file_tag = ""                         # 크롭 재판독에 넘길 태그

    # ── 클라이언트 ──────────────────────────────────────────
    @property
    def client(self):
        if self._client is None:
            # 진입점이 여러 개다(화면·하네스·스크립트). 각자 기억하게 두면
            # 잊힌다 — 여기서 한 번 더 확인하고 사람이 읽을 오류를 낸다.
            from src import env
            env.require_key()
            from openai import OpenAI
            # 6R 에서 11건 중 7건이 타임아웃으로 죽었다. 스캔 1장이
            # 이미지 토큰으로 크기 때문에 기본 타임아웃이 빠듯하다.
            # `max_retries` 는 **전송 실패에만** 걸린다 — 모델이 답을 낸
            # 뒤에는 동작하지 않으므로 "다시 묻기" 가 아니다.
            self._client = OpenAI(timeout=180.0, max_retries=4)
        return self._client

    def _ask(self, model: str, system: str, text: str, png: str) -> dict:
        r = self.client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": [
                    {"type": "text", "text": text},
                    {"type": "image_url", "image_url": {
                        "url": f"data:image/png;base64,{_b64(png)}"}},
                ]},
            ],
            response_format={"type": "json_object"},
            max_completion_tokens=MAX_OUT,
        )
        u = r.usage
        self.calls.append({"model": model, "in": u.prompt_tokens,
                           "out": u.completion_tokens, "png": os.path.basename(png)})
        body = r.choices[0].message.content or "{}"
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            # 모델이 JSON 을 깨뜨렸다 — 삼키지 않고 사유를 남긴다(철학 5)
            raise ValueError(f"VLM 응답이 JSON 이 아니다: {body[:200]}") from None

    # ── ③-b 추출 ────────────────────────────────────────────
    def extract(self, path: str, triage: TriageResult,
                fields: Sequence) -> list[RawExtraction]:
        page = self._page_of(triage)
        png = self._png(path, page)
        spec = _field_spec(fields)
        # 정비·개조 보고서면 꼬리말 회사명이 시공사다 — 미리 경고한다
        warn = preprocess.caution_reason(path)
        note = ""
        if warn:
            note = ("주의: " + warn + "\n"
                    "하단 꼬리말의 회사명은 시공사일 수 있다. "
                    "제조사는 표·모델명·Note 에서 찾아라.\n\n")
        tag = getattr(triage, "file_tag", None) or ""
        self.file_tag = tag
        hint = ""
        if tag:
            # 다중 태그 페이지에서 이 파일의 자산을 가리는 유일한 근거다
            hint = ("이 파일의 태그: " + str(tag) + "\n"
                    "페이지에 태그가 여러 개 적혀 있으면 이 태그만 "
                    "engineering_tag_no 에 넣는다.\n")
        if minimal_mode():
            # 덧붙인 안내를 전부 뺀다 — 질문과 필드 정의만 남긴다
            note = hint = ""
        text = ("이 페이지에서 아래 필드를 판독하라.\n\n" + note + hint
                + "\n■ 필드\n" + spec
                + ("" if minimal_mode() else "\n" + _maker_table()))
        model = models.for_attempt(0).name
        sysmsg = (MINIMAL_SYSTEM if minimal_mode()
                  else (SYSTEM_EN if english_mode() else SYSTEM))
        if domain_mode():
            sysmsg = DOMAIN_LINE + sysmsg
        if nobbox_mode():
            # 규칙 4(bbox 설명)도 함께 뺀다 — 요구하지 않는 것을 설명하면
            # 프롬프트가 자기모순이 된다
            sysmsg = sysmsg.replace(_BBOX_ASK, _NOBBOX_ASK)
            sysmsg = re.sub(r"^4\. bbox 는.*?\n(?=\d\. )", "", sysmsg,
                            flags=re.S | re.M)
        data = self._ask_cached(model, sysmsg, text, png, path, page, fields)

        got = data.get("fields") or {}
        out = []
        for f in fields:
            d = got.get(f.key) or {}
            v = d.get("raw_value")
            v = None if v in ("", None, "null") else str(v).strip()
            out.append(RawExtraction(
                field_key=f.key, raw_value=v,
                raw_label=(d.get("raw_label") or None),
                # 기하 보정을 켰으면 보정본 기준 좌표를 원본 기준으로 되돌린다.
                bbox=self._bbox_to_original(png, self._bbox(d.get("bbox"))),
                page=page, confidence=float(d.get("confidence") or 0.0),
                parser=ParserType.VLM,
                source_locator=f"p{page}:vlm",
                note=_note_for(v, d),
            ))
        return out

    def _ask_cached(self, model: str, system: str, text: str, png: str,
                    path: str, page: int, fields) -> dict:
        """캐시가 있으면 재사용하고, 없으면 호출한 뒤 저장한다.

        역할  : 같은 입력에 같은 출력을 보장한다(철학 6). VLM API 는 완전한 결정론이
                아니므로 캐시 없이는 파이프라인 재실행 결과가 흔들린다.
        입력  : model/system/text/png — 호출 인자, path/page/fields — 캐시 키 재료
        출력  : 응답 dict
        부수효과: 캐시 읽기/쓰기, 미적중 시 네트워크 호출
        """
        if self.cache is None:                     # 캐시를 쓰지 않으면 바로 호출
            return self._ask(model, system, text, png)

        from .cache import cache_key, hash_source
        from .constants import PROMPT_VERSION

        # 프롬프트 버전에 모델과 필드 구성을 함께 넣는다 — 어느 하나만 바뀌어도
        # 응답이 달라지므로, 넣지 않으면 옛 응답을 잘못 재사용한다.
        #
        # ⚠ **응답을 바꾸는 것은 전부 키에 들어가야 한다.** 우리 A/B 는 환경변수로
        #   프롬프트 본문(D2S_PROMPT)과 이미지(D2S_UPSCALE·D2S_PREP)를 바꾼다.
        #   그것이 키에 없으면 2배 확대로 얻은 응답을 1배 실행이 재사용하고,
        #   비교 격자가 통째로 무력화된다. 조원이 d2d9255 에서 해상도로 같은
        #   결함을 고쳤다 — 이것은 그 나머지다.
        sysfp = hashlib.sha256(system.encode("utf-8")).hexdigest()[:12]
        # `le` 는 장변 목표(D2S_LONG_EDGE). 배율과 **다른 축**이므로 따로 넣는다 —
        # 같은 원본이라도 목표가 다르면 모델이 보는 이미지가 달라진다.
        imgfp = (f"up{upscale_factor():g}:cap{upscale_cap()}"
                 f":le{target_long_edge()}"
                 f":prep{'+'.join(prep_steps()) or '-'}")
        imgfp += ":dsk1" if self.deskew else ":dsk0"
        version = (f"{PROMPT_VERSION}:{model}:{','.join(f.key for f in fields)}"
                   f":{sysfp}:{imgfp}")
        key = cache_key(hash_source(path), page, version)
        hit = self.cache.get(key)
        if hit is not None:                        # 적중 — 호출을 건너뛴다
            return json.loads(hit)
        data = self._ask(model, system, text, png)
        self.cache.put(key, json.dumps(data, ensure_ascii=False))
        return data

    # ── Loop A 재판독 ───────────────────────────────────────
    # 재판독 프롬프트에도 같은 주의를 준다 — 크롭이 열 제목을 잘라낼 수 있다
    def reread(self, path: str, f, prev: RawExtraction,
               attempt: int = 1) -> RawExtraction | None:
        """bbox 크롭만 다시 본다. bbox 가 없으면 재판독하지 않는다.

        bbox 없이 페이지 전체를 다시 돌리면 같은 답이 나오거나(무의미) 다른
        답을 내라는 압박이 된다(환각). 그래서 `None` 을 돌려주고 사람에게 넘긴다.
        """
        if not prev.bbox:
            return None
        page = prev.page or 1
        crop = self._crop(path, page, prev.bbox)
        if crop is None:
            return None
        tier = models.for_attempt(attempt)
        al = " · ".join(f.aliases) if f.aliases else ""
        hint = ""
        if f.key == "engineering_tag_no" and self.file_tag:
            # 4R 에서 크롭이 태그 목록 전체를 다시 삼켰다 —
            # 전체 프롬프트의 방어를 크롭에도 준다
            hint = (" 이 파일의 태그는 " + str(self.file_tag) + " 다. "
                    "여러 개가 나열되어 있으면 이 태그만 답하라.")
        text = ("이 영역에서 「" + f.name + "」 에 해당하는 값을 판독하라."
                + (" 문서에서 이 항목은 " + al + " 로 적히기도 한다." if al else "")
                + hint
                + " Min/Nor/Max 열이 보이면 Normal 열을 읽어라.")
        d = self._ask(tier.name, REREAD_SYSTEM, text, crop)
        v = d.get("raw_value")
        v = None if v in ("", None, "null") else str(v).strip()
        return RawExtraction(
            field_key=f.key, raw_value=v,
            raw_label=(d.get("raw_label") or prev.raw_label),
            bbox=prev.bbox, page=page,
            confidence=float(d.get("confidence") or 0.0),
            parser=ParserType.VLM,
            source_locator=f"p{page}:vlm:crop",
            note=f"{tier.why} ({tier.name})",
        )

    # ── 보조 ────────────────────────────────────────────────
    def _page_of(self, triage: TriageResult) -> int:
        sel = getattr(triage, "selected_page", None)
        if sel is not None:
            return int(getattr(sel, "page", sel) or 1)
        for t in getattr(triage, "targets", []) or []:
            p = getattr(t, "page", None)
            if p:
                return int(p)
        return 1

    def _png(self, path: str, page: int) -> str:
        """페이지를 PNG 로 렌더한다. `deskew=True` 면 기하 보정까지 적용한다.

        보정을 적용하면 원본 ↔ 보정본의 좌표 대응(`PageTransform`)을 보관한다.
        모델은 보정본을 보고 답하므로, bbox 를 원본 기준으로 되돌려야 화면
        하이라이트가 어긋나지 않는다(`_bbox_to_original`).
        """
        out = preprocess.render_pages(path, self.render_dir, pages=[page])
        if not out:
            raise RuntimeError(f"렌더 실패: {path} p{page}")
        png = out[0]
        if self.deskew:                            # 기하 보정 — 기본 꺼짐
            png = self._deskewed(png)
        conditioned = _conditioned(png)            # 픽셀 조정 — 확대·구멍 제거
        # 확대는 정규화 bbox(0~1)를 바꾸지 않는다. 다만 **경로가 바뀌므로**
        # 기하 보정 기록을 새 경로로 옮겨 두어야 역변환이 그것을 찾는다.
        if conditioned != png and png in self._transforms:
            self._transforms[conditioned] = self._transforms[png]
        return conditioned

    def _deskewed(self, png: str) -> str:
        """렌더된 PNG 에 방향·기울기 보정을 적용하고 보정본 경로를 돌려준다.

        입력  : png — 렌더된 PNG 경로
        출력  : 보정본 PNG 경로 (보정할 것이 없으면 원래 경로)
        부수효과: 보정본을 render_dir 에 쓰고 변환 기록을 self._transforms 에 남긴다.
                 실패해도 예외를 올리지 않는다 — 보정은 보조 기능이므로 원본으로
                 진행하는 편이 낫다. 다만 사유는 로그에 남긴다(철학 5).
        """
        from PIL import Image                      # 지역 import — 기본 경로에서는 필요 없다
        from .preprocess import preprocess as geo_preprocess

        try:
            with Image.open(png) as opened:
                prepared = geo_preprocess(opened.copy())   # 방향·기울기·해상도
            transform = prepared.transform
            if not (transform.orientation_degrees or abs(transform.deskew_degrees) > 0):
                return png                          # 보정할 것이 없으면 그대로 (파일도 늘리지 않는다)
            out = os.path.join(self.render_dir, f"deskew_{os.path.basename(png)}")
            prepared.image.save(out, "PNG")
            self._transforms[out] = transform       # bbox 역변환에 쓴다
            return out
        except Exception as exc:                    # 보조 기능 실패로 추출 전체를 막지 않는다
            print(f"[vlm] 기하 보정 실패 — 원본으로 진행: {png} ({type(exc).__name__}: {exc})")
            return png

    def _bbox_to_original(self, png: str, bbox):
        """보정본 기준 정규화 bbox 를 원본 기준 정규화 bbox 로 되돌린다.

        입력  : png — 모델에 보낸 이미지 경로, bbox — 정규화 bbox 또는 None
        출력  : 원본 기준 정규화 bbox (보정이 없었으면 입력 그대로)
        부수효과: 없음
        """
        transform = self._transforms.get(png)      # 보정이 적용된 이미지인가
        if transform is None or bbox is None:
            return bbox                             # 보정이 없었으면 되돌릴 것도 없다
        pixels = transform.norm_bbox_to_original(bbox)          # 원본 픽셀 좌표로
        x0, y0, x1, y1 = transform.clamp_to_original(pixels)    # 페이지 경계 안으로
        width, height = transform.original_size                 # 정규화의 분모
        w = float(width) if width else 1.0
        h = float(height) if height else 1.0
        return (x0 / w, y0 / h, x1 / w, y1 / h)                 # 다시 정규화

    @staticmethod
    def _bbox(v) -> tuple[float, float, float, float] | None:
        """정규화 0~1 로 들어와야 한다. 벗어나면 버린다 — 틀린 좌표는 없는 것보다 나쁘다."""
        if not isinstance(v, (list, tuple)) or len(v) != 4:
            return None
        try:
            b = tuple(float(x) for x in v)
        except (TypeError, ValueError):
            return None
        if not all(0.0 <= x <= 1.0 for x in b) or b[0] >= b[2] or b[1] >= b[3]:
            return None
        return b

    def _crop(self, path: str, page: int, bbox) -> str | None:
        """bbox 주변을 잘라 낸다. **라벨 칸을 반드시 포함한다.**

        4R 실측에서 배운 것 — 라벨 없이 값만 잘라 보내면 상위 모델도 틀린다.
        크롭 안에 손글씨 값이 여러 개 들어 있는데 어느 항목인지 알 방법이
        없기 때문이다(`10-FV-002` 를 `10-FV-003` 으로 냈다).

        그래서 **왼쪽을 x=0 까지 연다.** 표의 항목명은 행 왼쪽에 있으므로
        이렇게 하면 라벨과 값이 같은 화면에 들어온다. 반대편 표까지 넣지는
        않는다 — x1 에서 8% 만 더 준다.

        세로는 3.5% — bbox 가 한 행(약 1%) 어긋나는 것을 실측했으므로
        위아래로 두세 행이 함께 보여야 정렬을 확인할 수 있다.
        """
        from PIL import Image
        png = self._png(path, page)
        try:
            with Image.open(png) as im:
                w, h = im.size
                x0, y0, x1, y1 = bbox
                pady = 0.035
                box = (0,                                   # 라벨 칸까지 왼쪽 전부
                       max(0, int((y0 - pady) * h)),
                       min(w, int((x1 + 0.08) * w)),
                       min(h, int((y1 + pady) * h)))
                if box[2] - box[0] < 8 or box[3] - box[1] < 8:
                    return None
                out = os.path.join(self.render_dir,
                                   f"crop_p{page}_{int(x0*1000)}_{int(y0*1000)}.png")
                piece = im.crop(box)
                # 재판독은 **한 행짜리 좁은 띠**라 더 키워도 토큰이 적게 는다.
                # 실측에서 남은 오류의 16%가 자릿수는 같고 한두 글자만 다른
                # 글자 오독이다(5↔3 · 8↔9 · 로마자 II↔IV). 2배 확대가 최대
                # 개선이었던 것과 같은 표적이므로, 크롭에는 배율을 더 준다.
                k = crop_zoom()
                if k > 1.0:
                    piece = piece.resize(
                        (max(1, int(piece.width * k)),
                         max(1, int(piece.height * k))), Image.LANCZOS)
                piece.save(out, "PNG")
                return out
        except Exception:
            return None

    # ── 비용 ────────────────────────────────────────────────
    def cost_summary(self) -> dict[str, Any]:
        by = {}
        for c in self.calls:
            b = by.setdefault(c["model"], {"calls": 0, "in": 0, "out": 0})
            b["calls"] += 1
            b["in"] += c["in"]
            b["out"] += c["out"]
        return by
