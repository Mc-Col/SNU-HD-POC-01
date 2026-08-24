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
import json
import os
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
    """모델명 → 제조사. 제조사가 안 적힌 문서가 많아 함께 넣는다."""
    rules = schema.manufacturer_rules()
    if not rules:
        return ""
    out = ["", "■ 모델명으로 제조사를 아는 표 (문서에 제조사가 없을 때만 쓴다)"]
    for r in rules:
        out.append(f"- {' / '.join(r.get('prefix') or [])} → {r.get('to')}"
                   f" ({r.get('kind', '')})")
    out.append("이 표로 채운 값은 raw_label 에 \"(모델명으로 판단)\" 을 적어라.")
    return "\n".join(out)


SYSTEM = """너는 컨트롤밸브 데이터시트를 읽는 판독기다. 추론하지 말고 판독한다.

규칙
1. 문서에 **적혀 있는 글자 그대로** 옮긴다. 단위·대소문자·기호를 바꾸지 않는다.
2. 문서에 없으면 raw_value 를 null 로 둔다. **절대 만들어내지 않는다.**
   빈칸·해당없음·미기재는 모두 null 이다.
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
8. 값이 있는 행을 착각하지 않도록, 반드시 **그 값의 왼쪽 항목명을 함께 읽어
   raw_label 에 적는다.** 항목명과 값이 어긋나면 그 판독은 틀린 것이다.

출력은 JSON 하나다:
{"fields": {"<field_key>": {"raw_value": "...", "raw_label": "...",
            "bbox": [x0,y0,x1,y1], "confidence": 0.0}}}
값이 없는 필드도 raw_value: null 로 포함한다."""

REREAD_SYSTEM = """너는 문서의 한 영역을 판독한다.

이 영역에 **문자 그대로 무엇이 적혀 있는지** 보고하라. 이전 판독과 같은
값이면 같다고 답하라. 다르게 답해야 할 이유는 없다.

출력은 JSON 하나다:
{"raw_value": "...", "raw_label": "...", "confidence": 0.0}
읽을 수 없으면 raw_value 를 null 로 둔다."""


class VlmParser:
    """계약 `ParserModule` 구현. OpenAI 호환 API 를 쓴다."""

    def __init__(self, client=None, render_dir: str | None = None,
                 only_mvp: bool = False):
        self._client = client
        self.render_dir = render_dir or os.path.join(tempfile.gettempdir(), "d2s_vlm")
        self.only_mvp = only_mvp
        os.makedirs(self.render_dir, exist_ok=True)
        self.calls: list[dict[str, Any]] = []      # 비용 추적 — 로그에 남긴다

    # ── 클라이언트 ──────────────────────────────────────────
    @property
    def client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI()
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
        tag = getattr(triage, "file_tag", None) or ""
        hint = ""
        if tag:
            # 다중 태그 페이지에서 이 파일의 자산을 가리는 유일한 근거다
            hint = ("이 파일의 태그: " + str(tag) + "\n"
                    "페이지에 태그가 여러 개 적혀 있으면 이 태그만 "
                    "engineering_tag_no 에 넣는다.\n")
        text = ("이 페이지에서 아래 필드를 판독하라.\n\n" + hint
                + "\n■ 필드\n" + spec + "\n" + _maker_table())
        model = models.for_attempt(0).name
        data = self._ask(model, SYSTEM, text, png)

        got = data.get("fields") or {}
        out = []
        for f in fields:
            d = got.get(f.key) or {}
            v = d.get("raw_value")
            v = None if v in ("", None, "null") else str(v).strip()
            out.append(RawExtraction(
                field_key=f.key, raw_value=v,
                raw_label=(d.get("raw_label") or None),
                bbox=self._bbox(d.get("bbox")),
                page=page, confidence=float(d.get("confidence") or 0.0),
                parser=ParserType.VLM,
                source_locator=f"p{page}:vlm",
                note="" if v is not None else "문서에서 찾지 못함",
            ))
        return out

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
        text = (f"이 영역에서 「{f.name}」 에 해당하는 값을 판독하라."
                + (f" 문서에서 이 항목은 {al} 로 적히기도 한다." if al else "")
                + " Min/Nor/Max 열이 보이면 Normal 열을 읽어라."
                  " 열 제목이 잘려 보이지 않으면 confidence 를 낮춰라.")
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
        out = preprocess.render_pages(path, self.render_dir, pages=[page])
        if not out:
            raise RuntimeError(f"렌더 실패: {path} p{page}")
        return out[0]

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
        """bbox 주변을 여유 있게 잘라 낸다. 라벨이 값 왼쪽에 있으므로 좌측을 더 준다."""
        from PIL import Image
        png = self._png(path, page)
        try:
            with Image.open(png) as im:
                w, h = im.size
                x0, y0, x1, y1 = bbox
                padx, pady = 0.10, 0.02
                box = (max(0, int((x0 - padx) * w)), max(0, int((y0 - pady) * h)),
                       min(w, int((x1 + 0.02) * w)), min(h, int((y1 + pady) * h)))
                if box[2] - box[0] < 8 or box[3] - box[1] < 8:
                    return None
                out = os.path.join(self.render_dir,
                                   f"crop_p{page}_{int(x0*1000)}_{int(y0*1000)}.png")
                im.crop(box).save(out, "PNG")
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
