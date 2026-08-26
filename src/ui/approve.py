# -*- coding: utf-8 -*-
"""사전 승인 — Loop C 의 입구.

**이것이 이 과제를 도구가 아니라 시스템으로 만든다.** 사람이 한 번 판단하면
그 판단이 규칙 파일에 남아 다음 실행부터 자동으로 적용된다.

지켜야 할 것 넷 — 전부 이 파일에 코드로 박혀 있다.

1. **실행이 끝난 뒤 한 번에.** 문서마다 물으면 1,021건에서 수백 번 클릭이 되고,
   그러면 읽지 않고 승인하게 된다 — 승인이라는 안전장치가 형식만 남는다.
   그래서 원천이 *실행 단위 파일*(`runs/raw/<id>/vocab_candidates.json`)이다.
2. **빈도순.** `vocabulary.as_rows()` 가 이미 그 순서로 준다.
3. **기본값은 무시.** 승인이 의도적 행위여야 한다.
4. **가장 가까운 허용값은 보여주기만.** 자동으로 고치지 않는다.

그리고 하나 더 — **기계가 `schema/rules.yaml` 을 쓰지 않는다.** 여기서는
병합할 YAML 조각을 만들어 `runs/.../vocab_approved.yaml` 에 두고, 규칙 파일에
넣는 것은 사람이 한다. 모델의 오독이 영구 규칙이 되면 안 되고, 규칙 파일이
"도메인 전문가가 읽고 고치는 산출물" 이라는 성질을 잃으면 안 된다.
"""
from __future__ import annotations

import io
import json
import os
from datetime import date

import streamlit as st

from src import schema
from src.hooks import hooks
from src.ui import session

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW_DIR = os.path.join(ROOT, "runs", "raw")
CAND_FILE = "vocab_candidates.json"
OUT_FILE = "vocab_approved.yaml"
FIXTURE = os.path.join(ROOT, "fixtures", "ui", "sample_vocab_candidates.json")

IGNORE, KEEP, ALIAS = "무시", "별개의 값", "다른 표기"


# ── 원천 ──────────────────────────────────────────────────────

def _runs_with_candidates() -> list[str]:
    if not os.path.isdir(RAW_DIR):
        return []
    out = []
    for name in sorted(os.listdir(RAW_DIR)):
        if os.path.exists(os.path.join(RAW_DIR, name, CAND_FILE)):
            out.append(name)
    return out


def load(path: str) -> tuple[dict, list[dict]]:
    with io.open(path, encoding="utf-8") as f:
        doc = json.load(f)
    return doc.get("summary") or {}, list(doc.get("rows") or [])


def _session_rows() -> tuple[dict, list[dict]]:
    """이번 화면 세션에서 관측한 것. 문서 한 건을 돌려본 직후에 쓴다."""
    from src.validate.domain import vocabulary
    return vocabulary.summary(), vocabulary.as_rows()


# ── 화면 ──────────────────────────────────────────────────────

def render() -> None:
    st.subheader("사전 승인 — 허용 어휘 밖에서 관측된 값")
    st.caption("**값을 바꾸지 않았습니다.** 오기일 수도, 어휘가 아직 좁은 것일 수도 "
               "있습니다. 승인하면 어휘가 자라고 다음 실행부터 통과합니다.")

    summary, rows, src_dir, label = _pick_source()
    if rows is None:
        _back()
        return
    if not rows:
        st.success("승인 대기 중인 후보가 없습니다.")
        _back()
        return

    c = st.columns(4)
    c[0].metric("후보", summary.get("candidates", len(rows)))
    c[1].metric("관측 건수", summary.get("observations", sum(
        r.get("count", 0) for r in rows)))
    c[2].metric("필드", summary.get("fields", len({r["field_key"] for r in rows})))
    c[3].metric("원천", label)

    st.warning("가장 가까운 허용값은 **보여주기만** 합니다. 자동으로 고치지 않습니다 — "
               "`C5`(Cr-Mo 합금강)와 `CS`(탄소강)는 한 글자 차이지만 다른 재질이고 "
               "사용 온도 한계가 다릅니다.", icon="⚠️")

    lo = st.slider("건수 하한 — 낮은 것부터 걷어내며 봅니다", 1,
                   max(2, max(r.get("count", 1) for r in rows)), 1)
    shown = [r for r in rows if r.get("count", 1) >= lo]
    st.caption(f"{len(shown)}건 표시 · {len(rows) - len(shown)}건 숨김 "
               f"(빈도순 — 40건에서 나온 표현과 1건짜리는 무게가 다릅니다)")

    _header()
    for i, r in enumerate(shown):
        _row(i, r)

    st.divider()
    _emit(shown, src_dir, label)


def _pick_source():
    runs = _runs_with_candidates()
    opts = [f"실행 {r}" for r in runs]
    if os.path.exists(FIXTURE):
        opts.append("합성 픽스처 (시험용)")
    opts.append("이번 화면 세션에서 관측한 것")

    pick = st.selectbox("어느 실행의 후보를 볼까요", opts,
                        help="전수 실행 결과는 `--emit` 을 준 실행의 보관 폴더에 "
                             "남습니다 — runs/raw/<id>/vocab_candidates.json")
    if pick.startswith("실행 "):
        name = pick[3:]
        d = os.path.join(RAW_DIR, name)
        s, rows = load(os.path.join(d, CAND_FILE))
        return s, rows, d, name
    if pick.startswith("합성"):
        s, rows = load(FIXTURE)
        return s, rows, os.path.join(ROOT, "runs", "ui-session"), "픽스처"
    s, rows = _session_rows()
    return s, rows, os.path.join(ROOT, "runs", "ui-session"), "이번 세션"


COLS = [1.5, 1.7, 0.5, 1.3, 1.5, 2.6]


def _header() -> None:
    for c, name in zip(st.columns(COLS),
                       ["필드", "값", "건수", "문서", "원문 항목명 · 가까운 허용값",
                        "판단 (기본 = 무시)"]):
        c.markdown(f"<div class='d2s-head'>{name}</div>", unsafe_allow_html=True)


def _key(r: dict) -> str:
    return f"{r['field_key']}::{r['value']}"


def _row(i: int, r: dict) -> None:
    c = st.columns(COLS, vertical_alignment="center")
    try:
        name = schema.get(r["field_key"]).name
    except KeyError:
        name = r["field_key"]

    c[0].markdown(f"<div class='d2s-key'>{name}</div>"
                  f"<div class='d2s-code'>{r['field_key']}</div>",
                  unsafe_allow_html=True)
    c[1].markdown(f"<div class='d2s-val'>{r['value']}</div>", unsafe_allow_html=True)
    c[2].markdown(f"<div class='d2s-val'>{r.get('count', 1)}</div>",
                  unsafe_allow_html=True)
    c[3].markdown(f"<div class='d2s-raw'>{r.get('docs', '')}</div>",
                  unsafe_allow_html=True)

    near = r.get("nearest") or ""
    c[4].markdown(f"<div class='d2s-raw'>{r.get('labels', '') or '—'}</div>"
                  + (f"<div class='d2s-note'>가까운 값 · {near}</div>" if near else ""),
                  unsafe_allow_html=True)

    with c[5]:
        # 값을 바꾸는 규칙은 correctable 필드에만 붙는다. 재질·안전 필드는
        # flag_only 라 애초에 선택지에 없다 — CS→C5 는 탄소강을 크롬몰리강으로
        # 바꿔치기하는 일이고, 그 판단을 화면에서 사람에게 맡기면 언젠가 누른다.
        correctable = bool(r.get("correctable"))
        opts = [IGNORE, KEEP] + ([ALIAS] if correctable else [])
        choice = st.radio("판단", opts, horizontal=True, index=0,
                          key=f"ap_{_key(r)}", label_visibility="collapsed")
        if choice == ALIAS:
            vocab = list(schema.allowed_values(r["field_key"]))
            idx = vocab.index(near) if near in vocab else 0
            st.selectbox("무엇의 다른 표기입니까", vocab, index=idx,
                         key=f"apt_{_key(r)}", label_visibility="collapsed")
        elif not correctable and choice == KEEP:
            st.caption("표시 전용 필드 — 어휘에만 추가됩니다")
    st.markdown("<div class='d2s-row'></div>", unsafe_allow_html=True)


# ── 산출 ──────────────────────────────────────────────────────

def decisions(rows: list[dict]) -> list[dict]:
    """화면에서 고른 판단을 모은다. 무시는 빼고."""
    out = []
    for r in rows:
        choice = st.session_state.get(f"ap_{_key(r)}", IGNORE)
        if choice == IGNORE:
            continue
        target = (st.session_state.get(f"apt_{_key(r)}")
                  if choice == ALIAS else r["value"])
        out.append({**r, "choice": choice, "target": target})
    return out


def seen_line(r: dict, today: str) -> str:
    """이 규칙이 왜 생겼는지 되짚는 유일한 단서. 사람이 손으로 안 적는 부분이다."""
    bits = [f"{r.get('docs') or '문서 미상'} ({r.get('count', 1)}건)"]
    if r.get("labels"):
        bits.append(f"원문라벨 '{r['labels']}'")
    bits.append(f"{today} 승인")
    return " · ".join(bits)


def to_yaml(picked: list[dict], today: str) -> str:
    """`schema/rules.yaml` 에 병합할 조각. **기계가 병합하지 않는다.**

    손으로 적힌 규칙 파일과 **같은 모양**으로 낸다 — `from` 은 인라인 리스트,
    `seen` 은 한 줄. 사람이 읽고 붙이는 것이므로 형식이 튀면 안 된다.
    """
    aliases: dict[str, list[dict]] = {}
    flag_only: dict[str, list[dict]] = {}
    for r in picked:
        bucket = aliases if r.get("correctable") else flag_only
        bucket.setdefault(r["field_key"], []).append(
            {"value": r["value"], "to": r.get("target") or r["value"],
             "seen": seen_line(r, today)})

    out = ["# 사전 승인 결과 — schema/rules.yaml 에 **사람이** 병합한다.",
           f"# 승인일 {today} · 검토자 {session.reviewer()}",
           "#",
           "# 기계가 규칙 파일을 직접 쓰지 않는다. 모델의 오독이 영구 규칙이 되고,",
           "# 규칙 파일이 도메인 전문가가 읽는 산출물이라는 성질을 잃기 때문이다.",
           ""]

    if aliases:
        out.append("value_aliases:")
        for key, items in aliases.items():
            out.append(f"  {key}:")
            for it in items:
                out.append(f"    - from: [{q(it['value'])}]")
                out.append(f"      to: {q(it['to'])}")
                out.append(f"      seen: {q(it['seen'])}")
        out.append("")

    if flag_only:
        out += ["enum_allowed_values:", "  flag_only:"]
        for key, items in flag_only.items():
            out.append(f"    {key}:")
            for it in items:
                out.append(f"      - {q(it['value'])}    # {it['seen']}")
        out.append("")
    return chr(10).join(out).rstrip() + chr(10)


def q(s: str) -> str:
    """YAML 안전 인용. 규칙 파일이 쓰는 방식과 같게 큰따옴표로 감싼다."""
    return json.dumps(str(s), ensure_ascii=False)


def _emit(rows: list[dict], src_dir: str, label: str) -> None:
    picked = decisions(rows)
    c1, c2 = st.columns([1.2, 3])
    with c1:
        make = st.button(f"승인 {len(picked)}건으로 YAML 만들기", type="primary",
                         disabled=not picked, use_container_width=True,
                         key="btn_make_yaml")
    with c2:
        if not picked:
            st.markdown("<div class='d2s-raw'>기본값은 무시입니다. "
                        "승인할 것만 고르세요</div>", unsafe_allow_html=True)
        else:
            st.markdown(f"<div class='d2s-raw'>{len(picked)}건 승인 대기 — "
                        f"나머지 {len(rows) - len(picked)}건은 무시됩니다</div>",
                        unsafe_allow_html=True)

    if not make:
        return

    today = date.today().isoformat()
    text = to_yaml(picked, today)
    os.makedirs(src_dir, exist_ok=True)
    out_path = os.path.join(src_dir, OUT_FILE)
    with io.open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)

    for r in picked:
        hooks.on_rule_edit(r["field_key"], None,
                           f"{r['choice']} → {r['target']}",
                           by=session.reviewer(), source="vocab_approve")

    st.success(f"{os.path.relpath(out_path, ROOT)} 에 저장했습니다. "
               f"아래를 `schema/rules.yaml` 의 해당 절에 **사람이** 병합합니다.")
    st.code(text, language="yaml")
    st.download_button("승인 YAML 내려받기", data=text.encode("utf-8"),
                       file_name=OUT_FILE, mime="text/yaml", key="btn_dl_yaml")


def _back() -> None:
    if st.button("≪ 돌아가기", key="btn_ap_back"):
        session.go(session.MAIN)
