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
from concurrent.futures import ThreadPoolExecutor, as_completed
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
# 채점기 전용 캐시 — 파이프라인 캐시와 절대 섞지 않는다(위 `cache` 설정 주석 참고)
SCORE_CACHE_DIR = os.path.join(ROOT, "runs", "vlm_score_cache")
# 문서 판독 동시 실행 수 — API 대기가 병목이라 올린 만큼 빨라진다.
# 속도 제한에 걸리지 않도록 보수적으로 잡는다.
PARALLEL_DOCS = 4

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
    rep: int                                           # 회차 — 같은 조건 재실행 구분
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
    rep: int = 1                                       # 회차 — 잡음 측정용
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



def _ask_sized(parser, cache, model: str, text: str, png: str,
               page: int, fields, rep: int = 1) -> dict:
    """해상도까지 구분하는 캐시를 얹어 VLM 을 한 번 호출한다.

    역할  : `VlmParser._ask_cached` 와 같은 일을 하되, 캐시 키를 **렌더된 이미지**
            로 만든다. 파이프라인 키는 원본 파일 바이트 기반이라 원본·확대·축소가
            같은 키로 뭉개진다 — 해상도가 독립 변수인 채점기에서는 치명적이다.
    입력  : parser — VlmParser, cache — ResponseCache 또는 None,
            model — 모델 ID, text — 사용자 프롬프트, png — 렌더 결과 경로,
            page — 페이지 번호, fields — 요청 필드 목록
    출력  : 응답 dict
    부수효과: 캐시 읽기/쓰기, 미적중 시 네트워크 호출
    """
    if cache is None:                                  # 캐시를 끄면 곧장 호출한다
        return parser._ask(model, SYSTEM, text, png)

    from src.parsers.vlm.cache import cache_key, hash_source
    from src.parsers.vlm.constants import PROMPT_VERSION

    with Image.open(png) as img:                       # 픽셀·모드·크기가 지문에 들어간다
        content = hash_source(image=img.convert(img.mode))
    # 모델과 필드 구성이 바뀌면 응답도 바뀌므로 버전 문자열에 함께 넣는다
    # 회차를 키에 넣어야 재실행이 캐시에 막히지 않는다 - 잡음 측정의 전제다
    version = f"{PROMPT_VERSION}:{model}:{','.join(f.key for f in fields)}:r{rep}"
    key = cache_key(content, page, version)            # 해상도는 content 에 이미 반영됨

    hit = cache.get(key)                               # 적중하면 네트워크를 타지 않는다
    if hit is not None:
        return json.loads(hit)

    data = parser._ask(model, SYSTEM, text, png)       # 미적중 — 실제 호출
    cache.put(key, json.dumps(data, ensure_ascii=False))
    return data


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
            out_dir: str, only_mvp: bool, cache, rep: int = 1) -> Run:
    """한 조합으로 골든셋 전체를 돌리고 채점한다.

    역할  : 문서마다 지정 페이지를 렌더 → 전문 판독 1회 → 정답 대조.
    입력  : model — 모델 ID, size — SIZES 키, rows — read_kit 결과,
            raw_root — 원본 뿌리, out_dir — 렌더 위치,
            only_mvp — MVP 9필드만 볼지, cache — 응답 캐시(None 가능)
    출력  : Run
    부수효과: 렌더 파일 생성, 캐시 미적중 시 API 호출
    """
    run = Run(model=model, size=size, rep=rep)
    parser = VlmParser(render_dir=out_dir, only_mvp=only_mvp, cache=cache)
    fields = list(schema.mvp_fields() if only_mvp else schema.all_fields())
    asked = {f.key for f in fields}                     # 채점 범위 = 물어본 필드
    text = f"이 페이지에서 아래 필드를 판독하라.\n\n■ 필드\n{_field_spec(fields)}\n"
    long_edge = SIZES[size]

    # 1단계: 렌더 (순차) — PIL 과 파일 쓰기를 한 스레드에 묶는다.
    # 렌더는 빠르므로 병렬로 얻을 이득이 없고, 스레드 안전만 잃는다.
    jobs = []                                          # (row, page, png) 목록
    for row in rows:
        path = find_file(raw_root, row["file"])
        if row["cls"] == "out_of_scope":               # 라벨러가 범위 밖으로 판단한 문서
            run.docs.append((row["doc_id"], row["file"], "제외(out_of_scope)"))
            continue
        if path is None:
            run.docs.append((row["doc_id"], row["file"], "파일 없음"))
            continue
        if os.path.splitext(path)[1].lower() in preprocess.EXCEL_EXT:
            # 운영에서 엑셀은 라우터가 텍스트 파서로 보낸다(src/router/__init__.py:127).
            # VLM 으로 채점하면 실제로 타지 않는 경로를 재는 셈이라 대상에서 뺀다.
            # (덧붙여 _render_excel 은 win32com + Excel 이 있어야 동작한다)
            run.docs.append((row["doc_id"], row["file"], "제외(엑셀 - 텍스트 파서 담당)"))
            continue
        # 라벨러가 적어 둔 사양표 페이지를 그대로 쓴다 — Triage 판정과 섞지 않는다
        page = int(row["spec_page"]) if row["spec_page"] else 1
        try:
            jobs.append((row, page, render_page(path, page, out_dir, long_edge)))
        except Exception as exc:                       # 실패를 삼키지 않는다(철학 5)
            run.errors.append(
                f"{row['doc_id']} {row['file']}: 렌더 {type(exc).__name__}: {exc}"[:200])
            run.docs.append((row["doc_id"], row["file"], f"렌더실패: {type(exc).__name__}"))

    # 2단계: 판독 (병렬) — 전체 시간의 대부분이 API 대기다.
    # 응답 순서는 뒤섞여도 되므로 순번에 담아 두고, 채점은 3단계에서 원래 순서대로 한다.
    def ask(job):
        """한 문서를 판독한다. 예외는 부른 쪽에서 처리하도록 그대로 올린다."""
        _, page, png = job
        # `_ask_cached` 는 키를 원본 파일 바이트로 만들어 해상도를 구분하지 못한다.
        # 채점기는 해상도가 독립 변수이므로 렌더 결과 자체를 키 재료로 쓴다.
        return _ask_sized(parser, cache, model, text, png, page, fields, rep)

    answers = {}                                       # 작업 순번 → 응답 또는 예외
    if jobs:
        with ThreadPoolExecutor(max_workers=PARALLEL_DOCS) as pool:
            futures = {pool.submit(ask, j): i for i, j in enumerate(jobs)}
            for fut in as_completed(futures):
                i = futures[fut]
                try:
                    answers[i] = fut.result()
                except Exception as exc:               # 한 문서 실패가 전체를 죽이지 않는다
                    answers[i] = exc

    # 3단계: 채점 (순차) — 자료구조를 한 스레드에서만 건드린다.
    for i, (row, page, _png) in enumerate(jobs):
        data = answers.get(i)
        if data is None or isinstance(data, Exception):
            run.errors.append(
                f"{row['doc_id']} {row['file']}: {type(data).__name__}: {data}"[:200])
            run.docs.append((row["doc_id"], row["file"], f"실패: {type(data).__name__}"))
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
                model=model, size=size, rep=rep, field_key=key, field_name=name,
                truth=t, uncertain=uncertain, got=raw,
                verdict=judge(t, raw, key),
                confidence=float(d.get("confidence") or 0.0)))

    for c in parser.calls:                             # 비용은 조합마다 따로 센다
        run.calls += 1
        run.tokens_in += c.get("in", 0)
        run.tokens_out += c.get("out", 0)
    return run



def _ok(verdict: str) -> bool:
    """성공 판정 — 텍스트 판과 같은 기준(정확·표기차이·정규화대기)."""
    return verdict in ("정확", "표기차이", "정규화대기")



def _stability(a, table, sizes, reps) -> None:
    """같은 조건 반복에서 판정이 얼마나 흔들리는지 표로 적는다.

    역할  : 흔들림률이 해상도 간 차이보다 크면 1회 비교는 신뢰할 수 없다.
    입력  : a — 출력 줄을 모으는 함수, table — 해상도별 {(회차,문서,필드): 성공},
            sizes — 해상도 목록, reps — 회차 목록
    출력  : 없음 (a 로 줄을 쌓는다)
    부수효과: 없음
    """
    a(f"  {'해상도':<10}{'칸':>8}{'항상성공':>10}{'항상실패':>10}"
      f"{'흔들림':>10}{'흔들림률':>10}")
    a("  " + "-" * 60)
    for z in sizes:
        per: dict[tuple, list[bool]] = {}               # (문서,필드) → 회차별 성공 여부
        for (_rep, doc, fk), ok in table[z].items():
            per.setdefault((doc, fk), []).append(ok)
        full = [v for v in per.values() if len(v) == len(reps)]   # 전 회차가 있는 칸만
        always = sum(1 for v in full if all(v))
        never = sum(1 for v in full if not any(v))
        flip = len(full) - always - never               # 회차마다 뒤집힌 칸
        pct = 100.0 * flip / len(full) if full else 0.0
        a(f"  {z:<10}{len(full):>8}{always:>10}{never:>10}{flip:>10}{pct:>9.1f}%")
    a("")
    a("  흔들림 = 회차마다 성공/실패가 바뀐 칸. 이 비율이 해상도 간 차이보다")
    a("  크면, 1회 실행 비교는 신뢰할 수 없다.")
    a("")

def compare_sizes(runs: list[Run]) -> str:
    """해상도를 칸 단위로 짝지어 비교하고 잡음을 함께 보고한다.

    역할  : 총점 비교는 문서 난이도 편차와 모델 비결정성에 묻힌다. 같은 문서·같은
            필드·같은 회차를 맞대응시켜 **승패만** 세면 두 교란이 모두 상쇄된다.
    입력  : runs — 모델 x 해상도 x 회차의 모든 실행
    출력  : 사람이 읽는 표 문자열
    부수효과: 없음
    """
    out: list[str] = []
    a = out.append
    models = sorted({r.model for r in runs})

    for model in models:
        mine = [r for r in runs if r.model == model]
        sizes = [z for z in SIZES if any(r.size == z for r in mine)]
        reps = sorted({r.rep for r in mine})
        if len(sizes) < 2 and len(reps) < 2:
            continue                                   # 비교할 축이 아무것도 없다

        # ── 회차별 성공률 — 잡음의 크기를 눈으로 본다 ──────────────────
        a("=" * 98)
        a(f"  회차별 성공률 — {model}   (같은 조건 재실행이 얼마나 흔들리는가)")
        a("=" * 98)
        a("  " + f"{'해상도':<8}" + "".join(f"{'회차' + str(r):>9}" for r in reps)
          + f"{'평균':>9}{'폭':>8}")
        a("  " + "-" * 60)
        for z in sizes:
            rates = [r.rate() for r in mine if r.size == z]
            if not rates:
                continue
            span = max(rates) - min(rates)
            a("  " + f"{z:<8}" + "".join(f"{v:>8.1f}%" for v in rates)
              + f"{sum(rates) / len(rates):>8.1f}%{span:>7.1f}%")
        a("")
        a("  폭 = 같은 조건 최고-최저. 해상도 간 차이가 이 폭보다 작으면 잡음이다.")
        a("")

        # ── 칸 단위 짝지은 비교 ────────────────────────────────────────
        # (회차, 문서, 필드) 를 열쇠로 삼아 해상도끼리만 맞댄다
        table: dict[str, dict[tuple, bool]] = {}
        for r in mine:
            slot = table.setdefault(r.size, {})
            for c in r.cells:
                slot[(r.rep, c.doc_id, c.field_key)] = _ok(c.verdict)

        if len(sizes) < 2:                             # 맞댈 상대가 없으면 건너뛴다
            a("=" * 98)
            a(f"  칸 안정성 — {model}   (같은 조건 {len(reps)}회에서 판정이 일치하는가)")
            a("=" * 98)
            _stability(a, table, sizes, reps)
            continue

        a("=" * 98)
        a(f"  해상도 짝지은 비교 — {model}   (같은 칸에서 어느 쪽이 이겼나)")
        a("=" * 98)
        a(f"  {'대결':<20}{'맞댄칸':>8}{'A만성공':>9}{'B만성공':>9}"
          f"{'차이':>8}{'판정':>26}")
        a("  " + "-" * 92)
        for i, x in enumerate(sizes):
            for y in sizes[i + 1:]:
                keys = set(table[x]) & set(table[y])
                a_only = sum(1 for k in keys if table[x][k] and not table[y][k])
                b_only = sum(1 for k in keys if table[y][k] and not table[x][k])
                diff = a_only - b_only
                n = a_only + b_only                    # 의견이 갈린 칸만이 정보다
                # 부호검정 정규근사 — n 이 작으면 판정을 보류한다
                if n < 10:
                    verdict = "표본 부족 - 판단 보류"
                else:
                    z_stat = abs(diff) / (n ** 0.5)    # p=0.5 귀무가설의 표준편차 = sqrt(n)/2 x2
                    if z_stat >= 2.58:
                        verdict = f"{x if diff > 0 else y} 우세 (강함)"
                    elif z_stat >= 1.96:
                        verdict = f"{x if diff > 0 else y} 우세"
                    else:
                        verdict = "차이 없음 - 잡음 범위"
                a(f"  {x + ' vs ' + y:<20}{len(keys):>8}{a_only:>9}{b_only:>9}"
                  f"{diff:>+8}{verdict:>26}")
        a("")
        a("  맞댄칸 중 양쪽 다 성공하거나 양쪽 다 실패한 칸은 정보가 없어 제외된다.")
        a("  판정은 갈린 칸(A만성공+B만성공)에 대한 부호검정이다.")
        a("")

        # ── 칸 흔들림 — 같은 조건에서 판정이 회차마다 바뀌는 비율 ──────
        if len(reps) > 1:
            a("=" * 98)
            a(f"  칸 안정성 — {model}   (같은 조건 {len(reps)}회에서 판정이 일치하는가)")
            a("=" * 98)
            _stability(a, table, sizes, reps)

    return "\n".join(out)


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
    ap.add_argument("--repeat", type=int, default=1,
                    help="같은 조건 반복 횟수. 2 이상이면 모델 비결정성(잡음)을 잰다")
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
        # 파이프라인 공유 캐시(runs/vlm_cache)를 쓰지 않는다.
        # 공유 캐시의 키는 `원본파일|페이지|프롬프트버전:모델:필드` 라서 **렌더 해상도가
        # 빠져 있다**. 확대본으로 얻은 답을 그 키로 저장하면, 원본 해상도로 도는
        # 파이프라인이 그 답을 자기 것으로 읽어간다 — 남의 결과를 오염시킨다.
        # 전용 디렉터리로 분리해 그 경로를 원천 차단한다.
        cache = ResponseCache(Path(SCORE_CACHE_DIR))

    reps = max(1, a.repeat)                            # 최소 1회
    print(f"  조합 {len(model_ids)} x {len(size_keys)} x {reps}회 = "
          f"{len(model_ids) * len(size_keys) * reps}개 실행 · 문서 {len(keep)}건\n")

    runs = []
    for m in model_ids:
        for s in size_keys:
            for r in range(1, reps + 1):
                # 회차 표시는 반복이 있을 때만 — 1회짜리 출력을 어지럽히지 않는다
                tag = f" (회차 {r}/{reps})" if reps > 1 else ""
                print(f"  실행 {m} / {s}{tag} …", flush=True)
                runs.append(run_one(m, s, keep, a.root, a.out,
                                    only_mvp=not a.all_fields, cache=cache, rep=r))

    print()
    print(render(runs))
    comparison = compare_sizes(runs)   # 해상도가 2종 이상일 때만 내용이 있다
    if comparison.strip():
        print(comparison)

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
