# -*- coding: utf-8 -*-
"""D2S 데모 웹 진입점.

    streamlit run app.py

여기는 라우팅만 한다. 화면 구현은 `src/ui/` 안에 있고, 그래야 모듈 소유 경계가
유지된다(이 파일은 루트에 있어야 streamlit 이 찾는다).
"""
from __future__ import annotations

import streamlit as st

from src import env
from src.ui import hitl, screens, session, theme

env.load()      # .env 는 프로세스 시작 시 한 번 읽는다 (VLM 키)

st.set_page_config(page_title="Datasheet 정보추출 Agent (PoC)",
                   page_icon="🔧", layout="wide")

ROUTES = {
    session.MAIN: screens.main_screen,
    session.UPLOAD: screens.upload_screen,
    session.CONFIRM: screens.confirm_screen,
    session.EXTRACT: screens.extract_screen,
    session.DONE: screens.done_screen,
}


def main() -> None:
    session.init()
    theme.inject_css()

    stage = session.stage()
    if stage == session.HITL:
        d = session.doc()
        if d is None:
            session.go(session.MAIN)
        hitl.render(d)
    else:
        ROUTES[stage]()


main()      # streamlit 은 스크립트를 매 상호작용마다 처음부터 다시 실행한다
