# -*- coding: utf-8 -*-
"""골든셋(라벨링 킷) 대비 VLM 파서 채점 — 내 모듈 자기 검증.

    python -m src.parsers.vlm.score_against_kit                     기본(1차 모델·원본 해상도)
    python -m src.parsers.vlm.score_against_kit --grid              모델 x 해상도 격자
    python -m src.parsers.vlm.score_against_kit --models luna sol --sizes 원본 확대

## 왜 이 파일이 필요한가

전처리·프롬프트·해상도를 바꿔도 **좋아졌는지 잴 자가 없으면** 개선이 아니라 변경일
뿐이다. 실측으로 두 번 확인했다 — bbox 품질은 집계 지표로도(무효·범위이탈·면적)
대리 지표로도(잉크 밀도) 재지지 않았고, 정답 대조만이 유효했다.

## 판정은 서경빈 선임 로직을 **그대로 재사용한다**

`src/parsers/text/score_against_kit.py` 의 판정 함수를 import 해서 쓴다.
같은 자로 재야 두 경로(텍스트·VLM)를 비교할 수 있고, 표기 차이 흡수 규칙
(`4"` vs `4 in`, `600#` vs `ANSI CLASS 600`)을 두 곳에 두면 한쪽만 낡는다.
**그 파일은 수정하지 않는다** — 읽기만 한다(철학 1).

판정 5단계 (서경빈 선임 정의)
    정확        정규화 후 완전히 같다
    표기차이    느슨한 정규화로 같다 (단위·대소문자 차이)
    정규화대기  표준값 매핑이나 포함 관계로 같다 (원문 "OPEN" → 표준 "FAIL OPEN")
    오답        위 어디에도 걸리지 않는다
    미추출      값이 없다

## 텍스트 판과 다른 점

    값 공급자   parse_excel / parse_pdf_text  →  VlmParser 의 전문 판독
    대상        pdf · xlsx                    →  **tif 포함 전 포맷**
    격자        없음                          →  모델 x 해상도 조합
    비용        0                             →  API 호출 (캐시가 적중하면 0)

## 이 도구가 재는 것은 `extract` 경로다

파이프라인의 `reread`(bbox 크롭 재판독)는 **추출 실패에만** 발동하고 TIER2 모델을
쓴다. 여기서는 전문 판독 1회만 돌려 조합을 비교한다 — 재판독까지 섞으면 무엇이
효과를 냈는지 가려지지 않는다.

## 주의

    · 정답이 `NA` · `판독불가` 인 칸은 채점에서 빠진다 (서경빈 선임 SKIP_VALUES)
    · 정답 앞 `?` 는 라벨러가 확신 없다는 표시다 — 채점은 하되 따로 표시한다
    · **AI 초안 행은 제외한다.** 자기 답을 자기가 채점하면 숫자가 무의미하다
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field as dc_field
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _load_env() -> None:
    """`.env` 를 찾아 읽는다 — 워크트리에는 `.env` 가 체크아웃되지 않는다.

    역할  : `OPENAI_API_KEY` 를 환경에 올린다. 이미 있으면 아무것도 하지 않는다.
    부수효과: 환경 변수를 채운다. 파일이 없으면 조용히 넘어간다(호출부가 키 부재를 알린다).
    """
    if os.environ.get("OPENAI_API_KEY"):
        return
    try:
        from dotenv import load_dotenv
    except ImportError:                                # 의존성이 없으면 환경 변수에 맡긴다
        return
    here = Path(ROOT)
    # 워크트리(.claude/worktrees/<name>) 라면 본 체크아웃에도 `.env` 가 있다
    for candidate in (here / ".env", *(p / ".env" for p in here.parents)):
        if candidate.is_file():
            load_dotenv(candidate)
            return


_load_env()

from PIL import Image                                                  # noqa: E402

from src import preprocess, schema                                     # noqa: E402
from src.parsers.text.field_index import FieldIndex                    # noqa: E402
from src.parsers.text.sections import SectionIndex                     # noqa: E402
# ── 판정 로직 재사용 (이 모듈은 읽기만 한다) ────────────────────
from src.parsers.text.score_against_kit import (                       # noqa: E402
    SKIP_VALUES,
    _cell_text,
    _contains,
    _loose,
    _norm,
    _standardize,
    find_file,
    read_kit,
)
from src.parsers.vlm.openai_vlm import SYSTEM, VlmParser, _field_spec  # noqa: E402

DEFAULT_KIT = os.path.join(ROOT, "readme", "labeling_kit.xlsx")
DEFAULT_RAW = os.path.join(ROOT, "raw_file")
DEFAULT_OUT = os.path.join(ROOT, "runs", "vlm_score")

# 해상도 변형 — 장변 목표(px). None 은 렌더 원본 그대로.
#   우리 스캔은 150dpi 장변 1753px 이고 `plan_scale` 의 확대 트리거(목표의 절반,
#   1288px)보다 크므로 공용 전처리는 손대지 않는다. 그래서 여기서 직접 만든다.
#   실측(2026-08-26): 확대하면 입력 토큰이 3,838 → 6,804 로 늘어난다.
#   즉 모델이 실제로 더 많이 본다 — GPT-4o 의 "짧은 변 768 정규화" 는 적용되지 않는다.
SIZES: dict[str, int | None] = {
    "원본": None,
    "확대": 2576,
    "축소": 1024,
}

# 모델 별칭 — 긴 ID 를 매번 치지 않게
#   pro 는 Responses API 전용이고 추론 토큰이 커서 기본 격자에서는 뺀다
#   (실측: max_output_tokens 4,000 이면 추론이 전부 먹고 본문이 빈다. 16,000 필요).
MODEL_ALIAS: dict[str, str] = {
    "luna": "gpt-5.6-luna",
    "terra": "gpt-5.6-terra",
    "sol": "gpt-5.6-sol",
    "pro": "gpt-5.5-pro",
}
GRID_MODELS: tuple[str, ...] = ("luna", "sol")


@dataclass
class Cell:
    """채점 한 칸. 텍스트 판 `Cell` 과 같은 모양에 조합 정보를 더한다."""
    doc_id: str
    file: str
    labeler: str
    model: str
    size: str
    field_key: str
    field_name: str
    truth: str
    uncertain: bool
    got: object = None
    verdict: str = ""
    confidence: float = 0.0


@dataclass
class Run:
    """한 조합(모델 x 해상도)의 실행 결과."""
    model: str
    size: str
    cells: list[Cell] = dc_field(default_factory=list)
    docs: list[tuple[str, str, str]] = dc_field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    calls: int = 0
    errors: list[str] = dc_field(default_factory=list)

    def counts(self) -> Counter:
        """판정별 칸 수."""
        return Counter(c.verdict for c in self.cells)

    def rate(self) -> float:
        """성공률 — 정확·표기차이·정규화대기를 맞은 것으로 본다(텍스트 판과 같은 기준)."""
        n = self.counts()
        good = n["정확"] + n["표기차이"] + n["정규화대기"]
        return good / len(self.cells) * 100 if self.cells else 0.0


def judge(truth: str, got: object, key: str) -> str:
    """서경빈 선임 판정 순서를 그대로 따른다.

    역할  : 정답과 파서 값을 비교해 판정 문자열을 낸다.
    입력  : truth — 정답, got — 파서 값(None 가능), key — 필드 키
    출력  : 정확 / 표기차이 / 정규화대기 / 오답 / 미추출
    부수효과: 없음
    """
    if got is None or str(got).strip() == "":
        return "미추출"
    if _norm(got) == _norm(truth):
        return "정확"
    if _loose(got) == _loose(truth):
        return "표기차이"
    # 표기 매핑(rules.yaml)을 거쳐야 같아지는 것. **양쪽 모두** 접어서 비교한다 —
    # 정답지도 표기가 갈리기 때문이다(`600#` · `ANSI CLASS 300` · `300` 이 섞여 있다).
    if (_norm(_standardize(key, truth)) == _norm(_standardize(key, got))
            or _contains(got, truth) or _contains(truth, got)):
        return "정규화대기"
    return "오답"


def render_page(path: str, page: int, out_dir: str, long_edge: int | None) -> str:
    """지면을 PNG 로 렌더하고 필요하면 장변을 맞춘다.

    역할  : 해상도 변형을 만든다. 공용 `render_pages` 로 렌더한 뒤 이 함수가 크기만 바꾼다.
    입력  : path — 원본, page — 1부터, out_dir — 렌더 위치,
            long_edge — 목표 장변(None 이면 원본 그대로)
    출력  : PNG 경로
    부수효과: 파일을 쓴다
    """
    png = preprocess.render_pages(path, out_dir, pages=[page])[0]
    if long_edge is None:
        return png
    with Image.open(png) as img:
        if max(img.size) == long_edge:                 # 이미 목표 크기면 그대로
            return png
        scale = long_edge / max(img.size)
        # LANCZOS — 1비트 스캔을 확대할 때 계단이 덜 생긴다
        resized = img.resize((round(img.width * scale), round(img.height * scale)),
                             Image.LANCZOS)
        out = os.path.join(out_dir, f"{Path(png).stem}_{long_edge}.png")
        resized.save(out)
        return out


def run_one(model: str, size: str, rows: list[dict], raw_root: str,
            out_dir: str, only_mvp: bool, cache) -> Run:
    """한 조합으로 골든셋 전체를 돌리고 채점한다.

    역할  : 문서마다 지정 페이지를 렌더 → 전문 판독 1회 → 정답 대조.
    입력  : model — 모델 ID, size — SIZES 키, rows — read_kit 결과,
            raw_root — 원본 뿌리, out_dir — 렌더 위치,
            only_mvp — MVP 9필드만 볼지, cache — 응답 캐시(None 가능)
    출력  : Run
    부수효과: 렌더 파일 생성, 캐시 미적중 시 API 호출
    """
    run = Run(model=model, size=size)
    parser = VlmParser(render_dir=out_dir, only_mvp=only_mvp, cache=cache)
    fields = list(schema.mvp_fields() if only_mvp else schema.all_fields())
    asked = {f.key for f in fields}                     # 채점 범위 = 물어본 필드
    text = f"이 페이지에서 아래 필드를 판독하라.\n\n■ 필드\n{_field_spec(fields)}\n"
    long_edge = SIZES[size]

    for row in rows:
        path = find_file(raw_root, row["file"])
        if row["cls"] == "out_of_scope":               # 라벨러가 범위 밖으로 판단한 문서
            run.docs.append((row["doc_id"], row["file"], "제외(out_of_scope)"))
            continue
        if path is None:
            run.docs.append((row["doc_id"], row["file"], "파일 없음"))
            continue

        # 라벨러가 적어 둔 사양표 페이지를 그대로 쓴다 — Triage 판정과 섞지 않는다
        page = int(row["spec_page"]) if row["spec_page"] else 1
        try:
            png = render_page(path, page, out_dir, long_edge)
            data = parser._ask_cached(model, SYSTEM, text, png, path, page, fields)
        except Exception as exc:                       # 실패를 삼키지 않는다(철학 5)
            run.errors.append(
                f"{row['doc_id']} {row['file']}: {type(exc).__name__}: {exc}"[:200])
            run.docs.append((row["doc_id"], row["file"], f"실패: {type(exc).__name__}"))
            continue

        got_fields = data.get("fields") or {}
        run.docs.append((row["doc_id"], row["file"], f"채점 p{page}"))

        for key, (truth, name) in row["truth"].items():
            if key not in asked:
                # 요청하지 않은 필드를 미추출로 세면 성공률이 왜곡된다.
                # `--all-fields` 없이 MVP 9필드만 물었으면 그 9개만 채점한다.
                continue
            t = _cell_text(truth)
            uncertain = t.startswith("?")              # 라벨러가 확신 없다는 표시
            if uncertain:
                t = t[1:].strip()
            if _norm(t) in SKIP_VALUES:                # 정답이 없는 칸은 채점하지 않는다
                continue
            d = got_fields.get(key) or {}
            raw = d.get("raw_value")
            raw = None if raw in ("", None, "null") else str(raw).strip()
            run.cells.append(Cell(
                doc_id=row["doc_id"], file=row["file"], labeler=row["labeler"],
                model=model, size=size, field_key=key, field_name=name,
                truth=t, uncertain=uncertain, got=raw,
                verdict=judge(t, raw, key),
                confidence=float(d.get("confidence") or 0.0)))

    for c in parser.calls:                             # 비용은 조합마다 따로 센다
        run.calls += 1
        run.tokens_in += c.get("in", 0)
        run.tokens_out += c.get("out", 0)
    return run


def render(runs: list[Run]) -> str:
    """사람이 읽는 표로 만든다."""
    out: list[str] = []
    a = out.append

    a("=" * 98)
    a("  조합별 성적")
    a("=" * 98)
    a(f"  {'모델':<16}{'해상도':<8}{'칸':>5}{'정확':>6}{'표기':>6}{'정규화':>7}"
      f"{'오답':>6}{'미추출':>7}{'성공률':>8}{'호출':>6}{'입력토큰':>10}")
    a("  " + "-" * 94)
    for r in runs:
        n = r.counts()
        a(f"  {r.model:<16}{r.size:<8}{len(r.cells):>5}{n['정확']:>6}{n['표기차이']:>6}"
          f"{n['정규화대기']:>7}{n['오답']:>6}{n['미추출']:>7}{r.rate():>7.1f}%"
          f"{r.calls:>6}{r.tokens_in:>10,}")
        if r.errors:
            a(f"      실패 {len(r.errors)}건 — {r.errors[0][:72]}")

    a("")
    a("=" * 98)
    a("  필드별 성공률 (조합 전체 합산)")
    a("=" * 98)
    per: dict[str, Counter] = defaultdict(Counter)
    for r in runs:
        for c in r.cells:
            per[c.field_key][c.verdict] += 1
    a(f"  {'필드':<26}{'칸':>5}{'정확':>6}{'표기':>6}{'정규화':>7}{'오답':>6}"
      f"{'미추출':>7}{'성공률':>8}")
    a("  " + "-" * 74)
    for key in sorted(per, key=lambda k: sum(per[k].values()), reverse=True):
        n = per[key]
        tot = sum(n.values())
        good = n["정확"] + n["표기차이"] + n["정규화대기"]
        a(f"  {key:<26}{tot:>5}{n['정확']:>6}{n['표기차이']:>6}{n['정규화대기']:>7}"
          f"{n['오답']:>6}{n['미추출']:>7}{good / tot * 100:>7.1f}%")

    a("")
    a("=" * 98)
    a("  오답 상세 — 무엇을 고쳐야 하는가")
    a("=" * 98)
    bad = [c for r in runs for c in r.cells if c.verdict == "오답"]
    if not bad:
        a("  없음")
    for c in bad[:60]:
        mark = "?" if c.uncertain else " "
        a(f" {mark}[{c.doc_id}] {c.file[:28]:<30}{c.field_key:<22}"
          f"정답={c.truth[:16]!r:<18} 결과={str(c.got)[:16]!r:<18} "
          f"({c.model.split('-')[-1]}/{c.size} conf={c.confidence:.2f})")
    if len(bad) > 60:
        a(f"  … 외 {len(bad) - 60}건")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점."""
    ap = argparse.ArgumentParser(description="골든셋 대비 VLM 파서 채점")
    ap.add_argument("--kit", default=DEFAULT_KIT, help="라벨링 킷 경로")
    ap.add_argument("--root", default=DEFAULT_RAW, help="원본 문서 뿌리")
    ap.add_argument("--out", default=DEFAULT_OUT, help="렌더·결과 저장 위치")
    ap.add_argument("--models", nargs="*", default=["luna"],
                    help=f"모델 별칭 또는 ID. 별칭: {list(MODEL_ALIAS)}")
    ap.add_argument("--sizes", nargs="*", default=["원본"],
                    help=f"해상도 변형: {list(SIZES)}")
    ap.add_argument("--grid", action="store_true",
                    help=f"{list(GRID_MODELS)} x 전 해상도 (--models/--sizes 를 덮어쓴다)")
    ap.add_argument("--all-fields", action="store_true", help="28필드 전부 (기본 MVP 9)")
    ap.add_argument("--labeler", nargs="*", default=None,
                    help="이 라벨러의 행만 채점 (기본: AI 초안을 뺀 전부)")
    ap.add_argument("--limit", type=int, default=0, help="앞에서 N 건만 (빠른 확인용)")
    ap.add_argument("--no-cache", action="store_true", help="응답 캐시를 쓰지 않는다")
    a = ap.parse_args(argv)

    os.makedirs(a.out, exist_ok=True)
    six = SectionIndex.load()
    # 파서가 실제로 쓰는 것과 같은 인덱스로 킷을 읽는다
    ix = FieldIndex.load(section_names=six.name_map())
    rows, _, unresolved = read_kit(a.kit, ix)

    # 사람 라벨만 정답으로 쓴다 — AI 초안은 자기 채점이 되어 숫자가 무의미하다
    keep = []
    for row in rows:
        who = str(row.get("labeler") or "")
        if not who or "AI초안" in who:
            continue
        if a.labeler and who not in a.labeler:
            continue
        keep.append(row)
    if a.limit:
        keep = keep[:a.limit]

    print(f"킷 {a.kit}")
    print(f"  전체 {len(rows)}행 → 채점 대상 {len(keep)}행")
    if unresolved:
        print(f"  ⚠️ 필드 매핑 실패 {len(unresolved)}건: {unresolved[:5]}")
    if not keep:
        print("  채점할 행이 없다"); return 1

    model_ids = ([MODEL_ALIAS[k] for k in GRID_MODELS] if a.grid
                 else [MODEL_ALIAS.get(m, m) for m in a.models])
    size_keys = list(SIZES) if a.grid else [s for s in a.sizes if s in SIZES]
    if not size_keys:
        print(f"  해상도 이름이 잘못됐다. 가능: {list(SIZES)}"); return 2

    cache = None
    if not a.no_cache:
        from src.parsers.vlm.cache import ResponseCache
        from src.parsers.vlm.constants import DEFAULT_CACHE_DIR
        # str 을 그대로 주면 `cache_dir / key` 가 TypeError 를 낸다 (알려진 결함)
        cache = ResponseCache(Path(DEFAULT_CACHE_DIR))

    print(f"  조합 {len(model_ids)} x {len(size_keys)} = "
          f"{len(model_ids) * len(size_keys)}개 · 문서 {len(keep)}건\n")

    runs = []
    for m in model_ids:
        for s in size_keys:
            print(f"  실행 {m} / {s} …", flush=True)
            runs.append(run_one(m, s, keep, a.root, a.out,
                                only_mvp=not a.all_fields, cache=cache))

    print()
    print(render(runs))

    payload = [{
        "model": r.model, "size": r.size, "성공률": round(r.rate(), 1),
        "판정": dict(r.counts()), "호출": r.calls,
        "입력토큰": r.tokens_in, "출력토큰": r.tokens_out,
        "실패": r.errors,
        "칸": [vars(c) for c in r.cells],
    } for r in runs]
    dst = os.path.join(a.out, "score.json")
    with open(dst, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2, default=str)
    print(f"\n저장: {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
