# -*- coding: utf-8 -*-
"""추출 원문 보관 — 규칙을 고쳐도 다시 읽지 않는다

    from eval.store import RawStore

    st = RawStore("runs/raw/20260825-vlm")
    st.write(doc_id, recs, page, file=...)     # 추출 직후
    st.read()                                  # {doc_id: (recs, page)}

왜 필요한가
─────────────────────────────────────────────────────────────
지금 하네스는 **추출과 채점이 붙어 있다.** 표기 사전이나 허용 어휘를 고치면
문서를 처음부터 다시 읽어야 하고, 1,021건에서는 그 비용이 감당되지 않는다.

원문을 남겨 두면 규칙 변경의 재적용이 **0원**이 된다. 그리고 그것이
표기 사전 설계의 전제였다 — *"800번째 문서에서 새 표현을 발견하면 앞의
799건에 공짜로 소급 적용된다."*

무엇을 남기고 무엇을 남기지 않나
    남긴다      `RawExtraction` — 모델이 실제로 읽은 것
    남기지 않는다  정규화된 값 · 판정 · 상태
                → 그것들은 **규칙에서 다시 계산되는 것**이다. 저장하면
                  규칙과 어긋날 수 있고, 어긋난 줄 아무도 모른다.

한 줄에 한 항목(JSONL)인 이유는 실행이 중간에 죽어도 거기까지가 남기
때문이다. 문서 하나가 끝날 때마다 flush 한다.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, fields as dc_fields
from typing import Any, Iterable

from src.contracts import ParserType, RawExtraction

META = "_run.json"
DATA = "extractions.jsonl"


def _enc(v: Any) -> Any:
    """열거형은 값으로, 튜플은 목록으로. 되읽을 수 있는 형태만 남긴다."""
    if hasattr(v, "value"):
        return v.value
    if isinstance(v, tuple):
        return list(v)
    return v


class RawStore:
    """한 실행의 추출 원문."""

    def __init__(self, path: str):
        self.path = path
        self._fh = None

    # ── 쓰기 ────────────────────────────────────────────────
    def open(self, meta: dict | None = None) -> "RawStore":
        os.makedirs(self.path, exist_ok=True)
        if meta:
            with open(os.path.join(self.path, META), "w",
                      encoding="utf-8", newline="\n") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
        self._fh = open(os.path.join(self.path, DATA), "w",
                        encoding="utf-8", newline="\n")
        return self

    def write(self, doc_id: str, recs: Iterable[RawExtraction],
              page: int | None, **info) -> None:
        """문서 하나분을 쓴다. 실행이 죽어도 여기까지는 남는다."""
        if self._fh is None:
            self.open()
        for r in recs:
            row = {k: _enc(v) for k, v in asdict(r).items()}
            row["doc_id"] = doc_id
            row["page"] = page
            row.update(info)
            self._fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._fh.flush()

    def finish(self, parser=None) -> None:
        """모델·비용을 메타에 덧붙이고 닫는다.

        모델 목록은 **실행이 끝나야 안다** — 시작 시점에는 조건만 적는다.
        보관 파일의 형식을 아는 곳이 여기 하나여야 하므로 하네스가 아니라
        이쪽에 둔다.
        """
        meta = self.meta()
        calls = getattr(parser, "calls", None) or []
        if calls:
            meta["models"] = sorted({c["model"] for c in calls})
            meta["tokens_in"] = sum(c["in"] for c in calls)
            meta["tokens_out"] = sum(c["out"] for c in calls)
        os.makedirs(self.path, exist_ok=True)
        with open(os.path.join(self.path, META), "w",
                  encoding="utf-8", newline="\n") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        self.close()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()

    # ── 읽기 ────────────────────────────────────────────────
    def meta(self) -> dict:
        p = os.path.join(self.path, META)
        if not os.path.exists(p):
            return {}
        with open(p, encoding="utf-8") as f:
            return json.load(f)

    def read(self) -> dict[str, tuple[list[RawExtraction], int | None]]:
        """→ {doc_id: (추출목록, 페이지)}. 파일 순서를 지킨다."""
        p = os.path.join(self.path, DATA)
        if not os.path.exists(p):
            raise FileNotFoundError(
                f"보관된 추출이 없다: {p}\n"
                f"먼저 `--emit {self.path}` 로 한 번 돌려야 한다.")

        known = {f.name for f in dc_fields(RawExtraction)}
        out: dict[str, tuple[list, int | None]] = {}
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                doc = d.pop("doc_id", "")
                page = d.pop("page", None)
                kw = {k: v for k, v in d.items() if k in known}
                if kw.get("parser"):
                    kw["parser"] = ParserType(kw["parser"])
                if isinstance(kw.get("bbox"), list):
                    kw["bbox"] = tuple(kw["bbox"])
                recs, _ = out.setdefault(doc, ([], page))
                recs.append(RawExtraction(**kw))
        return {k: (v[0], v[1]) for k, v in out.items()}

    def summary(self) -> str:
        m = self.meta()
        try:
            data = self.read()
        except FileNotFoundError:
            return "보관 없음"
        cells = sum(len(v[0]) for v in data.values())
        bits = [f"문서 {len(data)}건", f"항목 {cells}칸"]
        if m.get("models"):
            bits.append(" · ".join(m["models"]))
        if m.get("stamp"):
            bits.append(m["stamp"])
        return " · ".join(bits)
