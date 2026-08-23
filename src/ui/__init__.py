# -*- coding: utf-8 -*-
"""⑦ HITL — 사람이 확정하는 화면 (Streamlit).

화면은 `DocumentResult` 만 소비한다. 필드 목록·필수여부·임계값·상태·잠금조건을
이 폴더 안에서 정의하지 않는다 — `src/contracts.py` 와 `schema/fields.yaml` 에서 온다.
그래서 이 화면은 계약의 시각화이고, 어긋나면 화면에서 바로 드러난다.
"""
