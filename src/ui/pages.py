# -*- coding: utf-8 -*-
"""화면 3 · 쪽 고르기 — 사람이 사양표를 지목한다.

쪽 선택을 사람이 하기로 정했다(2026-08-25). 그러면 이 화면은 있으면 좋은 것이
아니라 **파이프라인의 일부**다. 자동 선별에 쓰던 문서당 3,500 토큰이 사라진다.

세 가지를 지킨다.

1. **분류하지 않는다.** 사양표인지 아닌지는 사람이 본다. 텍스트 레이어가 있으면
   날짜·개정표기를 뽑아 배지로 붙여줄 뿐이다 — 스캔(71.9%)은 배지가 안 붙는다.
2. **후보가 둘 이상이면 축소 이미지를 믿지 않는다.** `10FV011` 은 사양표가 2장인데
   1986년 원본에 손으로 `OLD` 라고 적혀 있다. 썸네일에서는 그 글씨가 안 보이고,
   이건 숙련자도 틀린 케이스다. → 크게 나란히 놓고 사람이 읽는다.
3. **사람이 고를 때마다 규칙과 대조해 조용히 기록한다.** 자동 선택 정확도가
   사람이 일하는 동안 공짜로 측정된다. 추가 토큰이 들지 않는다.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field

import streamlit as st

from src import preprocess
from src.contracts import PageClass, PageInfo
from src.hooks import hooks
from src.ui import session

THUMB_DPI = 72          # 격자용. 고른 쪽만 200DPI 로 다시 뜬다
BIG_DPI = 200           # 비교 화면용 — 손글씨 "OLD" 가 읽혀야 한다
GRID_PX = 420           # 격자 셀에 실제로 보내는 크기
GRID_COLS = 4
MAX_THUMBS = 60         # 넘으면 잘라내되 **몇 장을 안 보여줬는지 말한다**


@dataclass
class PageView:
    """한 쪽에 대해 화면이 아는 것 전부. 판정이 아니라 관측이다."""
    page: int
    thumb: str | None = None
    has_text: bool = False
    text_len: int = 0
    date_raw: str = ""
    date_key: tuple | None = None
    date_ambiguous: bool = False
    marker: str = ""
    superseded: bool = False
    tags: list[str] = field(default_factory=list)

    @property
    def badges(self) -> list[tuple[str, str]]:
        """(문구, 색) — 텍스트 레이어에서 공짜로 얻은 것만."""
        out = []
        if self.superseded:
            out.append((f"폐기 표기 {self.marker}", "na"))
        elif self.marker:
            out.append((self.marker, "review"))
        if self.date_raw:
            label = self.date_raw + ("?" if self.date_ambiguous else "")
            out.append((label, "auto"))
        if self.tags:
            out.append(("태그 " + ", ".join(self.tags[:3]), "auto"))
        if not self.has_text:
            out.append(("텍스트 없음 · 스캔", "off"))
        return out


# ── 관측 ──────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def observe(path: str, mtime: float) -> list[PageView]:
    """쪽별 텍스트 관측. `mtime` 은 캐시 키일 뿐 쓰이지 않는다."""
    views = []
    for pt in preprocess.probe_pages(path):
        d = preprocess.parse_doc_date(pt.text)
        marker, superseded = preprocess.find_marks(pt.text)
        views.append(PageView(
            page=pt.page, has_text=pt.has_text_layer, text_len=pt.text_len,
            date_raw=d.raw, date_key=d.key if d else None,
            date_ambiguous=d.ambiguous, marker=marker, superseded=superseded,
            tags=preprocess.find_tags(pt.text)[:4],
        ))
    return views


def _dir(path: str, tag: str) -> str:
    stem = os.path.splitext(os.path.basename(path))[0][:40]
    out = os.path.join(tempfile.gettempdir(), "d2s_ui_pages", f"{stem}_{tag}")
    os.makedirs(out, exist_ok=True)
    return out


def _bucket(path: str, dpi: int) -> str:
    """이미지(tif 등)는 dpi 가 적용되지 않는다 — 스캔이 가진 화소가 상한이다.
    그래서 72·200 요청이 같은 결과이고, 폴더를 공유해 두 번 쓰지 않는다."""
    ext = os.path.splitext(path)[1].lower()
    return "orig" if ext in preprocess.IMAGE_EXT else str(dpi)


@st.cache_data(show_spinner="쪽 이미지를 만드는 중…")
def render_all(path: str, mtime: float, dpi: int = THUMB_DPI,
               pages: tuple[int, ...] | None = None) -> list[str]:
    """PNG 로 렌더한다. 썸네일은 72DPI — 61쪽 PDF 를 200DPI 로 다 뜨면 오래 걸린다."""
    try:
        return preprocess.render_pages(path, _dir(path, _bucket(path, dpi)),
                                       dpi=dpi,
                                       pages=list(pages) if pages else None)
    except Exception as e:                    # 실패를 삼키지 않는다
        hooks.on_error(None, "ui.render_pages", e)
        return []


@st.cache_data(show_spinner="쪽 이미지를 만드는 중…")
def thumbs(path: str, mtime: float, px: int = GRID_PX) -> list[str]:
    """격자에 보낼 축소본. 원본 화소를 그대로 보내면 브라우저가 버틴다.

    tif 는 dpi 요청이 무시되어 1240x1753 쯤으로 나온다. 54쪽 PDF·8쪽 tif 를
    그 크기로 격자에 밀어 넣으면 스크롤이 굳는다 — 여기서 한 번 줄인다.
    """
    from PIL import Image
    out = _dir(path, f"thumb{px}")
    made = []
    for src in render_all(path, mtime, THUMB_DPI):
        dst = os.path.join(out, os.path.basename(src))
        if not os.path.exists(dst):
            try:
                with Image.open(src) as im:
                    im.thumbnail((px, px * 2))
                    im.convert("RGB").save(dst, "PNG")
            except Exception as e:
                hooks.on_error(None, "ui.thumbs", e)
                dst = src                     # 줄이기 실패하면 원본으로 보여준다
        made.append(dst)
    return made


def _page_infos(views: list[PageView], cands: list[int]) -> list[PageInfo]:
    """규칙에게 물어보기 위한 형태로 옮긴다.

    사람이 후보로 지목한 쪽만 `SPEC` 으로 둔다 — 화면은 분류하지 않으므로,
    규칙에게 던지는 질문은 *"이 후보들 중 어느 것을 고를 것인가"* 다.
    분류가 아니라 **최신성 판단만** 대조하는 것이고, 그게 지금 규칙이 하는 일이다.
    """
    out = []
    for v in views:
        out.append(PageInfo(
            page=v.page,
            page_class=PageClass.SPEC if v.page in cands else PageClass.OTHER,
            has_text_layer=v.has_text, text_len=v.text_len, tags=list(v.tags),
            doc_date=v.date_raw, date_key=v.date_key,
            date_ambiguous=v.date_ambiguous,
            revision_marker=v.marker, superseded=v.superseded,
        ))
    return out


def rule_would_pick(views, cands, file_tag: str | None) -> tuple[int | None, str]:
    """규칙이라면 어느 쪽을 골랐을까. → (쪽 번호 | None, 사유)"""
    if not cands:
        return None, "후보 없음"
    picked, why = preprocess.pick_latest_spec(_page_infos(views, cands), file_tag)
    return (picked.page if picked else None), why


# ── 화면 ──────────────────────────────────────────────────────

def render(path: str) -> None:
    st.subheader("확인 · 어떤 쪽을 판독할까요")

    mtime = os.path.getmtime(path) if os.path.exists(path) else 0.0
    views = observe(path, mtime)
    if not views:
        _fallback(path)
        return

    info = preprocess.parse_filename(path)
    n_text = sum(1 for v in views if v.has_text)
    st.markdown(
        f"<span class='d2s-code'>{os.path.basename(path)} · {len(views)}쪽 · "
        f"텍스트 레이어 {n_text}쪽</span>", unsafe_allow_html=True)
    warn = preprocess.caution_reason(path)
    if warn:
        st.warning(f"이 문서는 {warn}")

    if len(views) == 1:
        _single(path, views, info)
        return

    # 실행 버튼을 격자보다 위에 둔다 — 61쪽 문서에서 끝까지 스크롤해야
    # 시작 버튼이 나오면 화면이 사람을 기다리게 만드는 게 아니라 괴롭힌다.
    # 체크박스 값은 session_state 에 남아 있으므로 격자보다 먼저 읽을 수 있다.
    cands = [v.page for v in views if st.session_state.get(f"pg_{v.page}")]
    _actionbar(path, views, info, cands)

    st.caption("사양표가 있는 쪽을 **모두** 고르세요. 두 장 이상이면 크게 비교해 "
               "보여드립니다 — 축소 이미지로는 손으로 쓴 `OLD` 같은 표기가 안 보입니다.")

    picked = _grid(path, mtime, views)
    session.page_candidates(picked)

    if len(picked) > 1:
        st.divider()
        _compare(path, mtime, views, info, picked)


def _actionbar(path, views, info, cands: list[int]) -> None:
    """돌아가기 · 판독 시작. 왼쪽이 취소, 오른쪽이 진행 — 읽는 순서와 같게."""
    cols = st.columns([1, 1.5, 1.5, 2.6], vertical_alignment="center")
    with cols[0]:
        if st.button("≪ 돌아가기", key="btn_back", use_container_width=True):
            session.reset()

    if not cands:
        cols[1].button("판독 시작 ≫", disabled=True, use_container_width=True,
                       key="btn_start_off")
        cols[3].markdown("<div class='d2s-raw'>사양표 쪽을 하나 이상 고르면 "
                         "시작할 수 있습니다</div>", unsafe_allow_html=True)
        return

    if len(cands) == 1:
        with cols[1]:
            if st.button(f"p{cands[0]} 으로 판독 시작 ≫", type="primary",
                         use_container_width=True, key="btn_start"):
                _start(path, views, info, cands[0], cands)
        _origin_note(cols[3], cands[0])
        return

    for col, pno in zip(cols[1:3], cands[:2]):
        with col:
            if st.button(f"p{pno} 으로 판독 ≫", type="primary",
                         use_container_width=True, key=f"top_pick_{pno}"):
                _start(path, views, info, pno, cands)
    more = f" (외 {len(cands) - 2}장은 아래에서)" if len(cands) > 2 else ""
    cols[3].markdown(f"<div class='d2s-note'>후보 {len(cands)}장 — 아래에서 크게 "
                     f"비교한 뒤 고르세요{more}</div>", unsafe_allow_html=True)


def _origin_note(col, page: int) -> None:
    if session.origin() == "fixture":
        col.markdown("<div class='d2s-raw'>합성 픽스처 — 판독하지 않고 저장된 "
                     "결과를 보여줍니다</div>", unsafe_allow_html=True)
    elif session.use_vlm():
        from src import models, schema
        meta = schema.summary()
        n = meta["mvp"] if session.only_mvp() else meta["field_count"]
        col.markdown(f"<div class='d2s-raw'>{models.for_attempt(0).name} 로 "
                     f"p{page} 판독 · {n}필드</div>", unsafe_allow_html=True)
    else:
        col.markdown("<div class='d2s-raw'>규칙 경로 — 스캔 문서는 값이 "
                     "나오지 않습니다</div>", unsafe_allow_html=True)


def _grid(path: str, mtime: float, views: list[PageView]) -> list[int]:
    small = thumbs(path, mtime)
    shown = views[:MAX_THUMBS]
    if len(views) > MAX_THUMBS:
        st.caption(f"⚠ {len(views)}쪽 중 앞 {MAX_THUMBS}쪽만 표시합니다 — "
                   f"뒤쪽에 사양표가 있으면 알려주세요")

    picked: list[int] = []
    for i in range(0, len(shown), GRID_COLS):
        cols = st.columns(GRID_COLS)
        for col, v in zip(cols, shown[i:i + GRID_COLS]):
            with col:
                # 고르는 수단과 판단 근거를 **이미지 위**에 둔다. 아래에 두면
                # 지면을 다 보고 다시 내려가야 해서 시선이 두 번 움직인다.
                if st.checkbox(f"p{v.page} · 사양표", key=f"pg_{v.page}"):
                    picked.append(v.page)
                _badges(v)
                src = small[v.page - 1] if v.page <= len(small) else None
                if src:
                    st.image(src, use_container_width=True)
                else:
                    st.markdown("<div class='d2s-raw'>(이미지 없음)</div>",
                                unsafe_allow_html=True)
    return picked


def _badges(v: PageView) -> None:
    from src.ui.theme import badge
    if not v.badges:
        return
    st.markdown(" ".join(badge(t, k) for t, k in v.badges),
                unsafe_allow_html=True)


def _single(path, views, info) -> None:
    _actionbar(path, views, info, [1])
    st.caption("한 쪽짜리 문서입니다. 그대로 판독합니다.")
    session.page_candidates([1])
    small = thumbs(path, os.path.getmtime(path))
    if small:
        c, _ = st.columns([1, 1.4])
        c.image(small[0], use_container_width=True)


def _compare(path, mtime, views, info, cands: list[int]) -> None:
    """후보가 둘 이상 — 크게 나란히. 여기서만 200DPI 로 뜬다."""
    st.markdown("##### 후보가 두 장 이상입니다 — 어느 쪽이 최신입니까")
    st.caption("날짜·개정표기는 텍스트 레이어에서 뽑은 것입니다. 스캔 문서는 "
               "배지가 붙지 않으니 **지면을 직접 읽으세요** — 손으로 쓴 `OLD`·"
               "`폐기` 표기가 결정적입니다.")

    big = render_all(path, mtime, BIG_DPI, tuple(cands))
    by_page = {p: b for p, b in zip(cands, big)}
    lookup = {v.page: v for v in views}

    for col, pno in zip(st.columns(len(cands)), cands):
        v = lookup.get(pno)
        with col:
            st.markdown(f"**p{pno}**")
            if by_page.get(pno):
                st.image(by_page[pno], use_container_width=True)
            if v:
                _badges(v)
            if st.button(f"p{pno} 으로 판독 ≫", key=f"pick_{pno}",
                         type="primary", use_container_width=True):
                _start(path, views, info, pno, cands)


def _start(path, views, info, page: int, cands: list[int]) -> None:
    """사람의 선택을 확정하고, 규칙이라면 어땠을지를 조용히 남긴다."""
    session.set_page(page)
    # 로그를 먼저 연다 — 열기 전에 부르면 이벤트가 버려진다
    session.ensure_run(os.path.basename(path), stage="page_select")
    auto, why = rule_would_pick(views, cands, info.tag_raw)

    # 자동이 '틀린 것' 과 '아예 판정 못 한 것' 은 다른 사실이다. 섞으면
    # 자동 선택 정확도가 실제보다 나쁘게도, 좋게도 보인다.
    verdict = ("판정불가" if auto is None
               else "일치" if auto == page else "불일치")
    hooks.on_human_action(
        os.path.basename(path), "__page__", "page_select",
        before=f"auto={auto or '판정불가'} · {why}",
        after=f"human=p{page} · 후보 {len(cands)}장 · {verdict}",
        by=session.reviewer())
    session.go(session.EXTRACT)


def _fallback(path: str) -> None:
    """쪽을 셀 수 없는 포맷 — 1쪽으로 진행한다."""
    st.info("이 포맷은 쪽 미리보기를 만들 수 없습니다. 1쪽으로 진행합니다.")
    session.page_candidates([1])
    c1, c2, _ = st.columns([1, 1.5, 2.5])
    with c1:
        if st.button("≪ 돌아가기", key="btn_back", use_container_width=True):
            session.reset()
    with c2:
        if st.button("판독 시작 ≫", type="primary", use_container_width=True,
                     key="btn_start"):
            session.set_page(1)
            session.go(session.EXTRACT)
