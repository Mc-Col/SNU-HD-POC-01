# -*- coding: utf-8 -*-
"""평가 하네스 — 골든셋을 숫자로 바꾼다

    python -m eval.harness --no-vlm              텍스트 경로만 (API 불필요)
    python -m eval.harness --stage text          텍스트 파서만 채점
    python -m eval.harness --by fmt              포맷별로 분해
    python -m eval.harness --holdout d009,d010   이 문서는 채점에서 제외(잠금)

무엇을 재는가 — 실패를 국소화한다
─────────────────────────────────────────────────────────────
"정확도 82%" 는 개선에 쓸 수 없다. 어느 단계가 틀렸는지 모르면 무엇을 고칠지
알 수 없기 때문이다. 그래서 실패를 다섯 갈래로 나눈다.

    페이지오선택   골든셋의 사양표 페이지와 다른 페이지를 골랐다
    미추출        그 페이지에서 값을 찾지 못했다
    근거없음오답   정답이 N/A 인데 값을 만들었다  ← 가장 나쁜 실패
    정규화대기     맞는 칸을 집었으나 표준값 변환이 남았다
    오답          다른 값을 집었다

`정규화대기` 는 서경빈 선임의 `score_against_kit.py` 에서 가져온 구분이다.
파서는 문서 원문(`OPEN`)을 내고 표준값(`FAIL OPEN`)은 ④ Normalize 몫이므로,
"맞는 칸을 집었나" 와 "최종값이 맞나" 는 다른 측정이다.

`근거없음오답` 을 따로 세는 이유는 이 과제의 핵심 주장이 **모르면 만들지
않는다** 이기 때문이다. 이 숫자가 0 이 아니면 나머지 정확도는 의미가 없다.

집계 축
    equipment_class   컨트롤밸브 / 레귤레이터 — 레귤레이터는 필드가 구조적으로
                      없으므로 섞어 평균하면 정확도가 부풀고 안전 필드가 희석된다
    fmt · vintage     포맷·연식별 — 어느 경로가 약한지
    field             필드별 — 어느 필드가 약한지

홀드아웃
    개발 중 보지 않는 문서를 지정한다. 결과를 보고 규칙을 고치면 측정이
    무효가 되므로, 홀드아웃은 1회 개봉하고 그 사실을 리포트에 남긴다.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field as dc_field

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 리포트는 한글이다. Windows 에서 출력을 리다이렉트하면 콘솔 코드페이지
# (cp949)가 쓰여 `—` 같은 문자에서 죽는다. 리포트·이력을 다 쓴 뒤 마지막
# 출력에서 죽으므로 **성공한 실행이 실패로 보인다.**
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from dataclasses import replace                     # noqa: E402

from eval import compare, escalate, groups, history  # noqa: E402
from eval.store import RawStore                      # noqa: E402
from src.contracts import FailureKind                # noqa: E402
from src.validate import domain                      # noqa: E402
from src.validate.domain import vocabulary           # noqa: E402
from eval.kit import KitRow, locate, read_kit       # noqa: E402
from src import schema                              # noqa: E402

def _show(raw, normed) -> str:
    """표에 쓸 표기. 정규화로 값이 바뀌었으면 둘 다 보인다.

    원문만 보이면 "왜 이게 오답이지" 를 알 수 없고, 정규화값만
    보이면 모델이 실제로 무엇을 읽었는지를 알 수 없다.
    """
    if raw is None and normed is None:
        return "—"
    if normed is None or str(raw) == str(normed):
        return str(raw)
    return f"{raw} → {normed}"


def _join(*parts) -> str:
    """비고를 이어 붙인다. 빈 것은 버린다."""
    return " | ".join(p for p in parts if p)


# 판정 — 나쁜 순서대로
VERDICTS = ("근거없음오답", "오답", "페이지오선택", "미추출", "정규화대기", "정확")
GOOD = ("정확",)
GRABBED = ("정확", "정규화대기")          # 맞는 칸을 집었다 (파서 책임 범위)


@dataclass
class Cell:
    doc_id: str
    field_key: str
    truth: str
    got: str | None
    verdict: str
    cls: str = groups.UNKNOWN
    fmt: str = ""
    vintage: str = ""
    uncertain: bool = False
    why: str = ""


@dataclass
class Result:
    cells: list[Cell] = dc_field(default_factory=list)
    docs: list[tuple[str, str, str]] = dc_field(default_factory=list)   # id, 파일, 상태
    page_calls: list[tuple[str, int | None, int | None]] = dc_field(default_factory=list)
    holdout: tuple[str, ...] = ()
    skipped_fields: list[str] = dc_field(default_factory=list)
    # 확인필요 표시 — (문서, 필드, 사유, **표시원**).
    # 표시원을 사유 문자열에서 되짚지 않는다 — 문구를 고치면 조용히 깨진다.
    flags: list[tuple[str, str, str, str]] = dc_field(default_factory=list)

    def counts(self, cells=None) -> dict[str, int]:
        c = Counter(x.verdict for x in (self.cells if cells is None else cells))
        return {v: c.get(v, 0) for v in VERDICTS}


# ── 판정 ────────────────────────────────────────────────────────

def judge(truth: str, got: str | None, raw: str | None, numeric: bool | None) -> tuple[str, str]:
    """한 칸의 판정. → (판정, 사유)"""
    if compare.is_na(truth):
        if got is None or compare.is_na(got):
            return "정확", ""
        return "근거없음오답", f"정답은 N/A 인데 {got!r} 을 만들었다"
    if got is None:
        return "미추출", f"정답 {truth!r} 을 찾지 못했다"
    if compare.same(truth, got, numeric):
        return "정확", ""
    # 표준값 변환만 남은 경우 — 파서는 원문을 낸다
    if raw is not None and compare.same(truth, raw, numeric):
        return "정확", ""
    if raw is not None and compare.norm_text(raw) and \
            compare.norm_text(raw) in compare.norm_text(truth):
        return "정규화대기", f"원문 {raw!r} 은 정답 {truth!r} 의 일부 — 표준값 변환 대기"
    if compare.norm_text(got) and compare.norm_text(got) in compare.norm_text(truth):
        return "정규화대기", f"{got!r} 은 정답 {truth!r} 의 일부 — 표준값 변환 대기"
    return "오답", compare.why(truth, got, numeric)


def _permanent(e: Exception) -> bool:
    """다시 시도해도 같은 결과인가 — 크레딧·인증·권한.

    일시 오류(타임아웃·연결 끊김·혼잡)와 구분한다. 일시 오류는 다음 문서에서
    성공할 수 있으므로 계속 간다. 영구 오류는 남은 문서를 전부 같은 이유로
    죽이므로 멈추는 편이 낫다 — 사유는 첫 건에서 이미 다 나왔다.
    """
    txt = f"{type(e).__name__} {e}".lower()
    keys = ("insufficient_quota", "credit_balance_exhausted",
            "no credits remaining", "authenticationerror", "permissiondenied",
            "invalid_api_key", "account is not active")
    return any(k in txt for k in keys)


def _numeric(field_key: str) -> bool | None:
    """이 필드를 숫자로 대조할지. `rules.yaml` 이 알려주지 않으면 자동 판정."""
    for m in schema.value_aliases(field_key):
        if m.get("numeric") is not None:
            return bool(m["numeric"])
    return None


# ── 채점 ────────────────────────────────────────────────────────

def score(rows: list[KitRow], extract, holdout: tuple[str, ...] = (),
          only_mvp: bool = False) -> Result:
    """골든셋을 채점한다.

    extract(row) → (values, raws, spec_page)
        values     {field_key: 최종값 | None}
        raws       {field_key: 문서 원문 | None}   (없으면 {})
        spec_page  고른 사양표 페이지 (없으면 None)
      파이프라인이든 파서 하나든, 이 모양만 맞추면 채점된다.
    """
    res = Result(holdout=tuple(holdout))
    keys = {f.key for f in (schema.mvp_fields() if only_mvp else schema.all_fields())}

    for row in rows:
        if row.doc_id in holdout:
            res.docs.append((row.doc_id, row.file, "홀드아웃 — 채점 제외"))
            continue
        if not row.path:
            res.docs.append((row.doc_id, row.file, "파일 없음"))
            continue
        try:
            got_ = extract(row)
            # 3-튜플(기존)과 4-튜플(부가정보 포함)을 모두 받는다
            values, raws, spec_page = got_[:3]
            extra = got_[3] if len(got_) > 3 else {}
        except Exception as e:
            res.docs.append((row.doc_id, row.file, f"처리 실패: {type(e).__name__} {e}"))
            if _permanent(e):
                # 다음 문서에서도 똑같이 실패한다. 11번 반복하면 시간만 쓰고
                # (전송 재시도 횟수가 곱해진다) 사유는 하나도 더 알 수 없다.
                # 일시 오류(타임아웃)는 여기 걸리지 않는다 — 한 건이 죽어도
                # 나머지는 측정 가치가 있으므로 계속 진행한다.
                for rest in rows[rows.index(row) + 1:]:
                    if rest.doc_id not in holdout:
                        res.docs.append((rest.doc_id, rest.file,
                                         "중단 — 앞선 영구 오류로 실행을 멈췄다"))
                break
            continue

        cls = groups.equipment_class(row.truth)
        page_ok = (spec_page is None or row.spec_page is None
                   or int(spec_page) == int(row.spec_page))
        res.page_calls.append((row.doc_id, row.spec_page, spec_page))
        res.docs.append((row.doc_id, row.file,
                         "채점" if page_ok else f"페이지 오선택 (정답 p{row.spec_page} / 선택 p{spec_page})"))

        absent = groups.expected_absent(cls)
        for key, truth in row.truth.items():
            if key not in keys:
                continue
            if key in absent and compare.is_na(truth):
                continue        # 이 설비에 구조적으로 없는 필드 — 쉬운 정답을 세지 않는다
            unc = str(truth).strip().startswith("?")
            if not page_ok:
                v, w = "페이지오선택", f"정답 p{row.spec_page} 대신 p{spec_page} 를 읽었다"
            else:
                v, w = judge(truth, values.get(key), (raws or {}).get(key), _numeric(key))

            # 확인필요 표시 — **조립은 `src/validate/domain` 한 곳에서만** 한다.
            # 화면도 같은 함수를 부른다. 두 곳에서 각자 조립하면 갈리고,
            # 그러면 "화면에서 본 것과 채점된 숫자가 다르다" 가 된다.
            got = values.get(key)
            if got is not None and not compare.is_na(str(got)):
                ctx_ = extra.get("extraction") or {}
                fl = domain.check_all(
                    schema.get(key), got, ctx_.get(key), ctx_,
                    confidence=(extra.get("confidence") or {}).get(key))
                for x in fl:
                    res.flags.append((row.doc_id, key, x.why, x.source))
                if any(x.source == "어휘" for x in fl):
                    domain.observe_all(schema.get(key), got, row.doc_id,
                                       label=(row.raw_label or {}).get(key, ""))
            res.cells.append(Cell(row.doc_id, key, truth, values.get(key), v,
                                  cls, row.fmt, row.vintage, unc, w))
    return res


# ── 리포트 ──────────────────────────────────────────────────────

def _rate(cells, wanted) -> str:
    if not cells:
        return "—"
    hit = sum(1 for c in cells if c.verdict in wanted)
    return f"{hit / len(cells) * 100:.0f}%"


def render(res: Result, by: str = "", escalations=None) -> str:
    n = res.counts()
    tot = sum(n.values())
    L = ["# 평가 하네스 — 골든셋 대조", ""]
    if res.holdout:
        L += [f"> 홀드아웃 {', '.join(res.holdout)} — 채점에서 제외했다. "
              f"개봉하면 그 사실을 여기 남긴다.", ""]
    if not tot:
        # 사유가 가장 필요한 순간이다. 예전엔 여기서 미채점 목록을 버리고
        # "경로를 확인할 것" 만 남겨 **틀린 곳을 보게 만들었다**(7R: 원인은
        # 경로가 아니라 API 크레딧 소진이었다).
        L += ["채점된 칸이 없다.", ""]
        if res.docs:
            L += ["| 문서 | 파일 | 사유 |", "|---|---|---|"]
            L += [f"| {d} | {f} | {st} |" for d, f, st in res.docs]
            L += [""]
        else:
            L += ["문서가 하나도 읽히지 않았다 — 킷 경로와 `--root` 를 확인할 것.",
                  ""]
        return "\n".join(L)

    scored = [d for d, _f, st in res.docs if st == "채점"]
    unscored = [(d, st) for d, _f, st in res.docs if st != "채점"]

    # 관대·엄격을 **함께** 낸다. 한 숫자로 줄이면 반드시 무언가가 가려진다 —
    # 단위를 값에 포함시킨 개선이 관대 기준에서는 전혀 안 보였고 부작용만
    # 보였다(2026-08-25). 하마터면 개선을 되돌릴 뻔했다.
    val = [c for c in res.cells if not compare.is_na(str(c.truth))]
    na_ok = [c for c in res.cells
             if c.verdict in GOOD and compare.is_na(str(c.truth))]
    strict = [c for c in val
              if c.verdict in GOOD and c.got is not None
              and compare.norm_text(c.truth) == compare.norm_text(c.got)]
    L += [f"- 문서 **{len(scored)}/{len(res.docs)}건 채점** · 칸 **{tot}개**",
          f"- **최종 정확도 {_rate(res.cells, GOOD)}** — 표준값까지 맞은 비율",
          f"- 칸 적중률 {_rate(res.cells, GRABBED)} — 맞는 칸을 집었는가 (파서 책임 범위)",
          ""]
    if val:
        L += ["| 기준 | 값 | 무엇을 세나 |", "|---|---|---|",
              f"| 관대 (헤드라인) | **{_rate(res.cells, GOOD)}** | "
              f"N/A 끼리 맞은 것 포함 · 단위 표기 차이 통과 |",
              f"| 값만 | {len(val) - sum(1 for c in val if c.verdict not in GOOD)}"
              f"/{len(val)} = **{(len(val) - sum(1 for c in val if c.verdict not in GOOD)) / len(val) * 100:.0f}%** | "
              f"정답이 N/A 인 칸을 뺀다 |",
              f"| **엄격** | {len(strict)}/{len(val)} = "
              f"**{len(strict) / len(val) * 100:.0f}%** | 정규화 후 **문자열이 정확히 같아야** |",
              f"| N/A 일치 | {len(na_ok)}칸 | 정확도가 아니라 **정직함**의 지표 |",
              "",
              "> 세 숫자를 함께 본다. **한 숫자로 줄이면 무언가가 가려진다** — "
              "단위를 값에 포함시킨 개선이 관대 기준에서는 보이지 않고 "
              "부작용만 보였던 사례가 있다.", ""]

    # 조용한 미측정을 헤드라인에 올린다 — 안 잰 것이 성공처럼 보이면 안 된다
    if unscored:
        L += [f"> ⚠ **{len(unscored)}건이 채점되지 않았다.** 이 정확도는 "
              f"{len(scored)}건에 대한 것이며 전체를 대표하지 않는다.", ""]
        why = defaultdict(list)
        for d, st in unscored:
            why[st.split(":")[0].split("(")[0].strip()].append(d)
        L += ["| 미채점 사유 | 문서 |", "|---|---|"]
        L += [f"| {k} | {', '.join(v)} |" for k, v in sorted(why.items())]
        L += [""]

    bad = n["근거없음오답"]
    if bad:
        L += [f"> ⚠ **근거없음오답 {bad}건** — 정답이 N/A 인데 값을 만들었다. "
              f"이 숫자가 0 이 아니면 나머지 정확도는 의미가 없다.", ""]
    else:
        L += ["> **근거없음오답 0건** — 문서에 근거가 없는 곳에서 값을 만들지 않았다.", ""]

    L += ["| 판정 | 건수 | 비율 |", "|---|---|---|"]
    for v in VERDICTS:
        L.append(f"| {v} | {n[v]} | {n[v] / tot * 100:.0f}% |")
    L += [""]

    # 설비 분류별 — 섞어 평균하면 안 되는 축
    L += ["## 설비 분류별", "",
          "레귤레이터는 필드가 구조적으로 없어 따로 낸다. 섞으면 정확도가 부풀고 안전 필드가 희석된다.",
          "", "| 분류 | 칸 | 최종 정확도 | 칸 적중률 |", "|---|---|---|---|"]
    for cls in (groups.CONTROL_VALVE, groups.REGULATOR, groups.UNKNOWN):
        cc = [c for c in res.cells if c.cls == cls]
        if cc:
            L.append(f"| {cls} | {len(cc)} | {_rate(cc, GOOD)} | {_rate(cc, GRABBED)} |")
    L += [""]

    # 안전·식별 필드 — 컨트롤밸브만
    safe_keys = {f.key for f in schema.safety_fields()}
    sc = [c for c in res.cells
          if c.field_key in safe_keys and groups.scoreable(c.cls, c.field_key)]
    if sc:
        L += ["## 안전·식별 필드", "",
              f"컨트롤밸브만으로 낸다 — 레귤레이터에는 `actuator_fail_action` 이 없다.", "",
              f"- 칸 {len(sc)}개 · **정확도 {_rate(sc, GOOD)}**",
              f"- 오적재 위험(오답 + 근거없음오답) "
              f"{sum(1 for c in sc if c.verdict in ('오답', '근거없음오답'))}건", ""]

    # 페이지 선택
    calls = [(d, g, p) for d, g, p in res.page_calls if g is not None and p is not None]
    if calls:
        ok = sum(1 for _, g, p in calls if int(g) == int(p))
        L += ["## 사양표 페이지 선택", "",
              f"- {ok}/{len(calls)} 정답 ({ok / len(calls) * 100:.0f}%)", ""]
        wrong = [(d, g, p) for d, g, p in calls if int(g) != int(p)]
        if wrong:
            L += ["| 문서 | 정답 | 선택 |", "|---|---|---|"]
            L += [f"| {d} | p{g} | p{p} |" for d, g, p in wrong] + [""]

    # 분해 축
    if by:
        L += [f"## {by} 별", "", f"| {by} | 칸 | 최종 정확도 | 칸 적중률 |", "|---|---|---|---|"]
        bucket = defaultdict(list)
        for c in res.cells:
            bucket[getattr(c, by, "")].append(c)
        for k in sorted(bucket):
            cc = bucket[k]
            L.append(f"| {k or '—'} | {len(cc)} | {_rate(cc, GOOD)} | {_rate(cc, GRABBED)} |")
        L += [""]

    # 약한 필드
    per = defaultdict(list)
    for c in res.cells:
        per[c.field_key].append(c)
    weak = sorted(((k, cc) for k, cc in per.items()
                   if any(x.verdict not in GOOD for x in cc)),
                  key=lambda kv: sum(1 for x in kv[1] if x.verdict in GOOD) / len(kv[1]))
    if weak:
        L += ["## 약한 필드", "", "| 필드 | 칸 | 정확도 | 주요 실패 |", "|---|---|---|---|"]
        for k, cc in weak[:12]:
            f = Counter(x.verdict for x in cc if x.verdict not in GOOD).most_common(1)
            L.append(f"| `{k}` | {len(cc)} | {_rate(cc, GOOD)} | {f[0][0] if f else '—'} |")
        L += [""]

    # 문서별
    L += ["## 문서별", "", "| 문서 | 파일 | 상태 |", "|---|---|---|"]
    L += [f"| {d} | {f} | {st} |" for d, f, st in res.docs] + [""]

    # 불확실 라벨
    unc = [c for c in res.cells if c.uncertain]
    if unc:
        L += [f"> 라벨러가 확신하지 못한 칸 {len(unc)}개(`?` 표시) 포함. "
              f"이 칸의 정확도는 참고치다.", ""]

    # 확인필요 표시의 품질 — 표시원이 둘이므로 **합쳐서** 센다
    #   ① 확신도 미달        코드 · 0원 · 임계는 fields.yaml
    #   ② 어휘 밖 값        코드 · 0원
    #   ③ 상위 모델 승격     유료
    # 하나만 세면 재현율이 실제보다 낮게 나온다.
    src_of: dict[tuple[str, str], set[str]] = {}
    for d, k, _why, src in res.flags:
        src_of.setdefault((d, k), set()).add(src)
    for e in (escalations or []):
        src_of.setdefault((e[0], e[1]), set()).add("승격")

    scored = {(c.doc_id, c.field_key) for c in res.cells}
    # 재현율의 분모는 **틀린 값이 실제로 들어간 칸**이다.
    #   미추출     값이 없다 → 3-상태에서 `근거없음` 이 되어 사람이 이미 본다
    #   페이지오선택 문서 단위 문제이지 값의 문제가 아니다
    # 이 둘을 넣으면 "표시 없이 마스터DB 로 간다" 는 말이 사실이 아니게 된다.
    DANGEROUS = ("근거없음오답", "오답", "정규화대기")
    bad = {(c.doc_id, c.field_key) for c in res.cells
           if c.verdict in DANGEROUS}
    flagged = set(src_of) & scored
    hit = flagged & bad
    if flagged or bad:
        L += ["## 확인필요 표시의 품질", "",
              "표시의 산출물은 값이 아니라 **사람에게 보낸다는 사실**이다. "
              "최종 정확도만 보면 이 효과가 안 잡힌다.", ""]
        if flagged:
            L += [f"- 표시가 붙은 채점 칸 **{len(flagged)}칸** 중 실제로 틀린 것 "
                  f"**{len(hit)}칸** — 정밀도 **{len(hit) / len(flagged) * 100:.0f}%**",
                  f"  (나머지 {len(flagged) - len(hit)}칸은 맞는데 검토를 부른다 "
                  f"— 사람 시간의 낭비다)"]
        if bad:
            L += [f"- 틀린 칸 **{len(bad)}칸** 중 표시가 붙은 것 **{len(hit)}칸** "
                  f"— 재현율 **{len(hit) / len(bad) * 100:.0f}%**",
                  f"  (표시 없이 틀린 {len(bad) - len(hit)}칸이 **그대로 "
                  f"마스터DB 로 간다** — 이쪽이 더 나쁘다)"]
        by_src = Counter(s2 for v in src_of.values() for s2 in v)
        if by_src:
            L += ["", "표시원별 — " + " · ".join(
                f"{k} {v}칸" for k, v in sorted(by_src.items()))]
        # 놓친 칸은 **필드 단위로 묶어서** 낸다. 칸을 전부 늘어놓으면
        # 수백 줄이 되고, 무엇을 고쳐야 하는지는 오히려 안 보인다.
        miss = sorted(bad - hit)
        if miss:
            per = Counter(k for _d, k in miss)
            L += ["", f"**표시를 놓친 {len(miss)}칸** — 조건을 넓힐 후보"
                  f"(필드별, 많은 순):", "",
                  "| 필드 | 놓친 칸 | 어휘가 있나 |", "|---|---|---|"]
            for k, n in per.most_common(12):
                has = "있음" if schema.allowed_values(k) else "**없음**"
                L.append(f"| `{k}` | {n} | {has} |")
            if len(per) > 12:
                L.append(f"| … 그 밖 {len(per) - 12}개 필드 | "
                         f"{sum(n for _k, n in per.most_common()[12:])} | |")
            L += ["", "> `어휘가 있나` 가 **없음**인 필드는 지금 표시를 만들 "
                  "수단이 아예 없다 — 어휘를 넓히는 것보다 그쪽이 먼저다.", ""]
        L += [""]

    # 사전 후보 — Loop C 의 입구
    cand = vocabulary.as_rows()
    if cand:
        L += ["## 사전 후보 — 사람 승인 대기", "",
              "허용 어휘 밖에서 관측된 값이다. **값을 바꾸지 않았다.** "
              "오기일 수도, 어휘가 아직 좁은 것일 수도 있다 — "
              "승인하면 어휘가 자라고 다음 실행부터 통과한다.", "",
              "| 필드 | 값 | 건수 | 문서 | 원문 항목명 | 가장 가까운 허용값 |",
              "|---|---|---|---|---|---|"]
        for r in cand:
            L.append(f"| `{r['field_key']}` | **{r['value']}** | {r['count']} | "
                     f"{r['docs']} | {r['labels']} | {r['nearest'] or '—'} |")
        L += ["", "> 가장 가까운 허용값은 **보여주기만** 한다. "
              "자동으로 고치지 않는다 — `C5`(Cr-Mo 합금강)와 `CS`(탄소강)는 "
              "한 글자 차이지만 다른 재질이다.", ""]

    # 승격 효과 — 상위 모델이 비용을 정당화하는가
    if escalations:
        by_verdict = Counter(e[4] for e in escalations)
        truth = {(c.doc_id, c.field_key): c.truth for c in res.cells}
        better = worse = same_wrong = 0
        rows_e = []
        for e in escalations:
            doc, key, first, second, verdict, note, why = e[:7]
            n1, n2 = (e[7], e[8]) if len(e) > 8 else (first, second)
            t = truth.get((doc, key))
            if t is None or verdict != "changed":
                continue
            # 헤드라인을 만드는 `judge()` 를 그대로 쓴다 — 두 숫자가 어긋날
            # 수 없다. 그리고 `정규화대기 → 오답` 도 악화로 잡힌다.
            # (5R d008: `Open`=정규화대기 → `Close`=오답. 최종 정확도로는
            #  둘 다 오답이지만 하나는 규칙 한 줄로 고쳐지고 하나는 아니다)
            num = _numeric(key)
            v1 = judge(t, n1, first, num)[0]
            v2 = judge(t, n2, second, num)[0]
            r1, r2 = VERDICTS.index(v1), VERDICTS.index(v2)   # 클수록 좋다
            if r2 > r1:
                better += 1
                mark = f"개선 ({v1}→{v2})"
            elif r2 < r1:
                worse += 1
                mark = f"**악화** ({v1}→{v2})"
            else:
                same_wrong += 1
                mark = f"변화없음 ({v1})"
            rows_e.append((doc, key, _show(first, n1), _show(second, n2),
                           t, mark, why))

        L += ["## 승격 효과 — 상위 모델이 비용을 정당화하는가", "",
              f"- 승격 대상 **{len(escalations)}칸** "
              f"(일치 {by_verdict.get('agree', 0)} · "
              f"값 변경 {by_verdict.get('changed', 0)} · "
              f"2차 실패 {by_verdict.get('kept', 0)} · "
              f"오류 {by_verdict.get('error', 0)})",
              f"- 값이 바뀐 것 중 — **개선 {better} · 악화 {worse} · "
              f"변화없음 {same_wrong}** (판정 등급 기준 — 헤드라인과 같은 "
              f"`judge()` 로 잰다)", ""]
        if better or worse:
            net = better - worse
            L += [f"> 순효과 **{net:+d}칸**. "
                  + ("상위 모델이 값을 했다." if net > 0 else
                     "상위 모델이 값을 하지 못했다 — 비용만 늘었다." if net < 0 else
                     "개선과 악화가 상쇄되었다.") + "", ""]
        if by_verdict.get("agree"):
            L += [f"> 일치 {by_verdict['agree']}칸 — 두 모델이 같은 값을 읽었다. "
                  f"확신을 높이는 근거이지만 **비용은 그대로 들었다.** "
                  f"승격 조건을 좁힐 여지가 여기 있다.", ""]

        if rows_e:
            L += ["| 문서 | 필드 | 1차(luna) | 2차(terra) | 정답 | 판정 | 승격 사유 |",
                  "|---|---|---|---|---|---|---|"]
            for doc, key, f1, f2, t, mark, why in rows_e:
                L.append(f"| {doc} | `{key}` | {f1} | {f2} | {t} | {mark} | {why} |")
            L += [""]

    # 실패 상세
    fails = [c for c in res.cells if c.verdict not in GOOD]
    if fails:
        L += ["## 실패 상세", "", "| 문서 | 필드 | 정답 | 추출 | 판정 | 사유 |",
              "|---|---|---|---|---|---|"]
        for c in sorted(fails, key=lambda x: (VERDICTS.index(x.verdict), x.doc_id)):
            L.append(f"| {c.doc_id} | `{c.field_key}` | {c.truth} | "
                     f"{c.got if c.got is not None else '—'} | {c.verdict} | {c.why} |")
        L += [""]

    L += ["---", "",
          "**100% 정확도는 증명할 수 없다.** 100건에서 오류 0건이면 95% 신뢰상한이 "
          "약 3% 다(rule of three). 표본이 11건이면 상한은 훨씬 넓다.", ""]
    return "\n".join(L)


# ── 추출기 — 지금 존재하는 것으로 채점한다 ──────────────────────

def make_text_extractor():
    """텍스트 파서만으로 추출한다(서경빈 선임 모듈). API 불필요.

    스캔 페이지는 텍스트가 없으므로 값이 비고, 그 사실이 `미추출` 로 잡힌다 —
    그것이 정직한 결과다. `--no-vlm` 이 재는 것이 바로 이 경계다.
    """
    from src.parsers.text.excel import parse_excel
    from src.parsers.text.pdf_text import parse_pdf_text

    def extract(row: KitRow):
        ext = os.path.splitext(row.path)[1].lower()
        page = row.spec_page
        if ext in (".xlsx", ".xlsm", ".xls"):
            res = parse_excel(row.path, sheets=[int(page)] if page else None)
        elif ext == ".pdf":
            res = parse_pdf_text(row.path, pages=[int(page)] if page else None)
        else:
            raise RuntimeError(f"{ext} 는 텍스트 경로가 없다 — VLM 담당")
        raws = {r.field_key: r.raw_value for r in res.records if r.found}
        return dict(raws), raws, page      # 파서는 원문만 낸다 (Normalize 이전)
    return extract


def _file_tag(row) -> str:
    """프롬프트에 넣을 태그. **문서 표기 그대로** 넘긴다.

    정규화 키(A10FV011)를 주면 문서에 없는 A 를 찾게 만든다.
    파일명에 태그가 없으면 골든셋 정답을 쓰지 않는다 — 그건 답을
    알려주는 것이고 측정이 무의미해진다.
    """
    from src import preprocess as pre
    info = pre.parse_filename(row.path or row.file or "")
    return info.tag_raw or ""


def make_vlm_extractor(only_mvp: bool = False, do_escalate: bool = False,
                       store=None):
    """VLM 으로 추출하고 ④ Normalize 까지 적용한다.

    파서는 문서 원문(`Close`)을 내고 표준값(`FAIL CLOSE`)은 Normalize 몫이다.
    둘을 함께 돌려주므로 하네스가 `정규화대기` 와 `오답` 을 구분할 수 있다.

    사양표 페이지는 **골든셋의 값을 쓴다.** 학습이 아니라 변수 통제다 —
    "VLM 이 값을 읽나" 와 "맞는 페이지를 찾나" 를 분리해서 재기 위한 것이고,
    페이지 선택은 Triage 가 붙은 뒤 따로 측정한다.
    """
    from src.contracts import DocumentClass, PageClass, PageInfo, TriageResult
    from src.parsers.vlm.openai_vlm import VlmParser
    from src.pipeline import DefaultNormalize

    parser = VlmParser()
    norm = DefaultNormalize()
    fields = schema.mvp_fields() if only_mvp else schema.all_fields()
    fields = [f for f in fields if f.source == "document"]   # 파생 필드는 VLM 몫이 아니다

    def extract(row: KitRow):
        page = int(row.spec_page or 1)
        tag = _file_tag(row)
        tri = TriageResult(
            source_path=row.path, document_class=DocumentClass.DATASHEET,
            file_tag=tag,
            pages=[PageInfo(page=page, page_class=PageClass.SPEC, selected=True)])
        recs = parser.extract(row.path, tri, fields)

        # ── 상위 모델 2차 판독 (독립 의견, 재시도가 아니다) ──
        if do_escalate:
            for idx, r in enumerate(recs):
                f = schema.get(r.field_key)
                why = escalate.reasons(f, r, tag)
                if not why:
                    continue
                try:
                    second = parser.reread(row.path, f, r, attempt=1)
                except Exception as e:
                    extract.escalations.append(
                        (row.doc_id, f.key, r.raw_value, None, "error",
                         f"{type(e).__name__}: {e}", "; ".join(why),
                         None, None))
                    continue
                value, verdict, note = escalate.settle(f, r, second)
                # 채점은 정규화 후 값으로 한다 — 승격 효과도 같은 잣대로 재야
                # 한다. 원문끼리 비교하면 `Open` vs `Fail Open` 이 오답으로
                # 잡혀 승격의 피해가 작게 보고된다 (4R 에서 실측).
                n1 = norm.run(r, f)[0]
                n2 = norm.run(second, f)[0] if second else None
                extract.escalations.append(
                    (row.doc_id, f.key, r.raw_value,
                     second.raw_value if second else None,
                     verdict, note, "; ".join(why), n1, n2))
                if verdict == "changed":
                    recs[idx] = replace(r, raw_value=value,
                                        note=_join(r.note, note))

        # 원문을 먼저 남긴다 — 규칙이 바뀌면 여기서부터 다시 계산한다.
        # 실행이 뒤에서 죽어도 여기까지는 보관된다.
        if store is not None:
            store.write(row.doc_id, recs, page, file=row.file)

        raws = {r.field_key: r.raw_value for r in recs if r.found}
        values = {}
        for r in recs:
            f = schema.get(r.field_key)
            v, _trace = norm.run(r, f)
            values[r.field_key] = v
        conf = {r.field_key: r.confidence for r in recs}
        return values, raws, page, {"confidence": conf,
                                    "extraction": {r.field_key: r for r in recs}}

    extract.parser = parser        # 비용 요약을 리포트에 쓴다
    extract.escalations = []       # 승격 효과를 리포트에 쓴다
    return extract


def make_replay_extractor(path: str, only_mvp: bool = False):
    """보관된 원문으로 다시 채점한다. **모델을 부르지 않는다.**

    정규화와 검증은 **지금 규칙으로** 다시 돈다 — 그것이 이 기능의 목적이다.
    표기 사전을 넓히고 이 함수로 돌리면 비용 없이 효과를 잴 수 있다.
    """
    from src.pipeline import DefaultNormalize

    st = RawStore(path)
    data = st.read()
    norm = DefaultNormalize()
    keys = {f.key for f in (schema.mvp_fields() if only_mvp
                            else schema.all_fields())}

    def extract(row: KitRow):
        got = data.get(row.doc_id)
        if got is None:
            raise KeyError(f"보관에 {row.doc_id} 가 없다 — 다른 실행의 보관인가")
        recs, page = got
        recs = [r for r in recs if r.field_key in keys]
        raws = {r.field_key: r.raw_value for r in recs if r.found}
        values = {}
        for r in recs:
            values[r.field_key] = norm.run(r, schema.get(r.field_key))[0]
        conf = {r.field_key: r.confidence for r in recs}
        return values, raws, page, {"confidence": conf,
                                    "extraction": {r.field_key: r for r in recs}}

    extract.store = st
    return extract


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="골든셋 평가 하네스")
    ap.add_argument("--kit", default="readme/labeling_kit.xlsx")
    ap.add_argument("--root", default="raw_file")
    ap.add_argument("--stage", default=None, choices=["text", "vlm", "pipeline"],
                    help="text = 텍스트 파서만 / vlm = VLM + Normalize / "
                         "pipeline = 전체 (모듈 구현 후). "
                         "--replay 와 함께 쓰면 생략 가능 — 저장된 단계를 쓴다")
    ap.add_argument("--no-vlm", action="store_true", help="VLM 경로를 쓰지 않는다")
    ap.add_argument("--only-mvp", action="store_true", help="MVP 9필드만")
    ap.add_argument("--by", default="fmt", choices=["", "fmt", "vintage", "cls"])
    ap.add_argument("--holdout", default="", help="쉼표로 구분한 문서ID")
    ap.add_argument("--out", default="runs/eval_report.md")
    ap.add_argument("--escalate", action="store_true",
                    help="확신도 미달·규칙 불일치·안전필드를 상위 모델로 2차 판독한다")
    ap.add_argument("--rules", default="",
                    help="다른 규칙 파일로 채점한다 (규칙 효과 비교용)")
    ap.add_argument("--emit", default="",
                    help="추출 원문을 이 폴더에 남긴다 (규칙 변경 후 재채점용)")
    ap.add_argument("--replay", default="",
                    help="보관된 원문으로 다시 채점한다 — 모델을 부르지 않는다")
    ap.add_argument("--note", default="",
                    help="이 실행에서 무엇을 바꿨는지. 이력 표에 남는다")
    a = ap.parse_args(argv)

    rows = read_kit(a.kit)
    missing = locate(rows, a.root)
    if read_kit.unmatched:
        print(f"⚠ 스키마에 없는 킷 컬럼: {', '.join(read_kit.unmatched)}", file=sys.stderr)
    if missing:
        print(f"⚠ 파일을 찾지 못함: {', '.join(missing)}", file=sys.stderr)

    # --replay 는 저장된 단계를 그대로 쓴다. 예전에는 --stage 를 생략하면
    # 기본값 text 로 떨어지고 --replay 가 **조용히 무시**됐다 — 스캔 문서가
    # 전부 처리 실패로 빠지면서 실제 89% 가 32% 로 보고됐다.
    # 받아들이고 무시하는 플래그는 오류보다 나쁘다.
    if a.replay:
        recorded = ""
        try:
            import json as _json
            with open(os.path.join(a.replay, "_run.json"),
                      encoding="utf-8") as fh:
                meta = _json.load(fh)
            recorded = str(meta.get("stage") or "")
        except Exception as e:
            print(f"⚠ {a.replay}/_run.json 을 읽지 못했다 ({type(e).__name__}). "
                  f"--stage 를 직접 지정할 것.", file=sys.stderr)
        if recorded and a.stage and a.stage != recorded:
            print(f"--replay 저장 단계는 '{recorded}' 인데 --stage {a.stage} 를 "
                  f"지정했다. 다른 단계로 재생하면 채점이 뜻을 잃는다.",
                  file=sys.stderr)
            return 2
        if recorded and not a.stage:
            a.stage = recorded
            print(f"재생 단계: {recorded} (저장된 값)", file=sys.stderr)
    if a.stage is None:
        a.stage = "text"

    if a.stage == "pipeline":
        print("전체 파이프라인 채점은 Triage·Router 구현 후 붙인다. "
              "지금은 --stage text 또는 --stage vlm 을 쓸 것.", file=sys.stderr)
        return 2
    if a.stage == "vlm" and a.no_vlm:
        print("--stage vlm 과 --no-vlm 은 함께 쓸 수 없다.", file=sys.stderr)
        return 2

    if a.stage == "vlm":
        from src import env
        if a.replay:
            extract = make_replay_extractor(a.replay, only_mvp=a.only_mvp)
            print(f"재생: {a.replay} — {extract.store.summary()}",
                  file=sys.stderr)
        else:
            env.require_key()
            store = None
            if a.emit:
                import time
                # 모델 목록은 실행이 끝나야 안다 — 여기서는 조건만 남기고
                # 비용 요약이 나온 뒤에 덧붙인다.
                store = RawStore(a.emit).open({
                    "stamp": time.strftime("%Y%m%d-%H%M%S"),
                    "stage": a.stage, "only_mvp": a.only_mvp,
                    "escalate": a.escalate, "kit": a.kit,
                })
            extract = make_vlm_extractor(only_mvp=a.only_mvp,
                                         do_escalate=a.escalate, store=store)
    else:
        extract = make_text_extractor()
    if a.rules:
        schema.use_rules(a.rules)
        print(f"규칙: {a.rules}", file=sys.stderr)
    vocabulary.reset()      # 후보 큐는 실행 단위로 모은다
    holdout = tuple(x.strip() for x in a.holdout.split(",") if x.strip())
    res = score(rows, extract, holdout, only_mvp=a.only_mvp)
    report = render(res, a.by,
                    escalations=getattr(extract, "escalations", None))
    parser = getattr(extract, "parser", None)
    store = getattr(extract, "store", None) if not a.emit else store
    if a.emit and store is not None:
        # 후보 큐를 파일로 남긴다 — 화면(다른 프로세스)이 승인하려면 필요하다.
        # 보관 폴더에 두어 어느 실행의 후보인지 묶어 둔다.
        import json
        with open(os.path.join(a.emit, "vocab_candidates.json"), "w",
                  encoding="utf-8", newline="\n") as f:
            json.dump({"summary": vocabulary.summary(),
                       "rows": vocabulary.as_rows()},
                      f, ensure_ascii=False, indent=2)
        store.finish(parser)          # 모델·비용을 메타에 덧붙이고 닫는다
        print(f"원문 보관: {store.path} — {store.summary()}", file=sys.stderr)

    if parser is not None and parser.calls:
        cost = parser.cost_summary()
        rows_md = ["", "## 비용", "",
                   "| 모델 | 호출 | 입력 토큰 | 출력 토큰 |", "|---|---|---|---|"]
        for m, c in sorted(cost.items()):
            rows_md.append(f"| `{m}` | {c['calls']} | {c['in']:,} | {c['out']:,} |")
        rows_md += ["", "이미지 토큰이 입력의 대부분이다. 비용을 줄이려면 "
                    "페이지 수를 먼저 줄인다 — 격자 1장으로 판정하는 이유다.", ""]
        report += "\n".join(rows_md)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(report)

    # 이력에 남긴다 — 덮어쓰이는 리포트만으로는 개선을 측정할 수 없다
    kept = history.archive(res, report, stage=a.stage,
                           note=a.note, parser=parser)

    print(report)
    print(f"\n저장: {a.out}", file=sys.stderr)
    print(f"보관: {kept}", file=sys.stderr)
    print("이력: docs/eval_history.md", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
