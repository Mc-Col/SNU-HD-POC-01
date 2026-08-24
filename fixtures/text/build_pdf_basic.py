# -*- coding: utf-8 -*-
"""fixtures/text/pdf_basic.pdf 생성 — 합성 데이터. 회사 문서를 넣지 않는다.

  python fixtures/text/build_pdf_basic.py

실물 사양서에서 관찰된 두 배치를 그대로 흉내 낸다.
  ① 머리글: "Label : Value" 한 덩어리
  ② 사양표: 좌우 2단 — 라벨 x67/x380, 값 x172/x448
"""
import os

import fitz

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdf_basic.pdf")

doc = fitz.open()
p1 = doc.new_page(width=595, height=842)

# ① 머리글 — 콜론 배치
for y, text in [(40, "Tag : 11-FV-999"),
                (52, "Manufacturer : FISHER"),
                (64, "Application : RECYCLE TO DHC FEED FILTERS")]:
    p1.insert_text((23, y), text, fontsize=8)

# ② 사양표 — 좌우 2단
ROWS = [
    (100, [(67, "Model No."),   (172, "667-ED")],
          [(380, "Fail/Air-To"), (448, "Close / Open")]),              # 복합 라벨
    (112, [(67, "Characteristic"), (172, "EQUAL %")],
          [(380, "Spring Range"),  (448, "0.4 - 2.0 bar")]),           # 미매핑
    (124, [(67, "Body Size"),   (172, "4 IN")],                        # 유사표현 미등록
          [(330, "Max Shutoff / Shutoff Class"), (480, "120 psi / ANSI IV")]),
    (172, [(67, "Size/Pressure Class/Body Form"), (250, "4 / 300 / Globe")],
          []),                                                          # 복합 라벨
]
for y, left, right in ROWS:
    for x, t in left + right:
        p1.insert_text((x, y), t, fontsize=8)

# Max / Nor / Min 열 머리글 + 값 행 — 표준은 Normal 값을 쓴다
p1.insert_text((67, 136), "Driving Cond.", fontsize=8)
for x, t in [(160, "Max"), (206, "Nor"), (247, "Min")]:
    p1.insert_text((x, 136), t, fontsize=8)

p1.insert_text((67, 148), "Viscosity", fontsize=8)
for x, t in [(160, "0.51"), (206, "0.72"), (247, "0.93")]:
    p1.insert_text((x, 148), t, fontsize=8)

# 값이 없는 라벨
p1.insert_text((67, 160), "Fluid Name", fontsize=8)

# 2페이지 — 먼저 찾은 값이 이겨야 한다
p2 = doc.new_page(width=595, height=842)
p2.insert_text((23, 40), "Manufacturer : MASONEILAN", fontsize=8)

doc.set_metadata({})
doc.save(OUT, garbage=4, deflate=True)
print("생성:", OUT)
