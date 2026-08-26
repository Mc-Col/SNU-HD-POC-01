# -*- coding: utf-8 -*-
"""이미지 조정 — 모델에 보내기 전 픽셀 손질 (공용)

    from src import imageprep

    imageprep.deskew_angle(png)          기울기 측정만 (도)
    imageprep.remove_punch_holes(png)    펀치 구멍을 흰색으로
    imageprep.upscale(png, 2)            보간 확대

`src/preprocess.py` 와 경계를 나눈다 — 그쪽은 **문서 판정**(파일명·페이지 선택·
텍스트 레이어), 여기는 **픽셀 조정**이다.

왜 여기 있는 것들만 있나 — 실측으로 걸러낸 결과다
─────────────────────────────────────────────────────────────
**확대** 는 재봤고 최대 개선이었다. 엄격 정확도 77% → 83%, 오답 45 → 29.
고쳐진 값이 `120→138` · `0.895→0.85` · `2.4→7.4` 처럼 **한두 획 차이로
틀렸던 숫자**였다.

**적응형 이진화는 넣지 않았다.** tif 40/40 이 1비트 Group 4 이고 렌더 후
중간톤 픽셀이 **0.0%** 다 — 임계값을 조정할 계조가 아예 없다. PDF 는 RGB 라
여지가 있지만 PDF 는 이미 정확도가 높은 쪽이라 표적이 어긋난다.

**선 제거·셀 크롭도 넣지 않았다.** 셀↔항목 매핑을 양식별로 만들어야 하는데
코퍼스에 1986~2021년 벤더 양식이 6종 이상이다. 그리고 크롭 재판독은
2026-08-24 에 이미 실패했다(92%→87%) — 크롭에 항목명이 없으면 모델이 어느
항목인지 모른다.

`cv2`·`scipy` 가 없는 환경이라 numpy 로 구현한다. Hough 대신 **투영 프로파일**
로 기울기를 재는데, 표 문서에서는 오히려 이쪽이 안정적이다 — 수평 괘선이
정렬될 때 행 합의 분산이 최대가 된다.
"""
from __future__ import annotations

import os

import numpy as np
from PIL import Image

__all__ = ["deskew_angle", "remove_punch_holes", "upscale", "as_gray"]


def as_gray(png: str) -> np.ndarray:
    """0(검정)~255(흰색) 회색조 배열. 1비트도 여기서 올린다."""
    with Image.open(png) as im:
        return np.asarray(im.convert("L"), dtype=np.uint8)


# ── 기울기 ──────────────────────────────────────────────────────

def deskew_angle(png: str, limit: float = 3.0, step: float = 0.25) -> float:
    """수평 괘선 기준 기울기(도). 양수는 시계방향으로 기울어진 것.

    **측정만 한다.** 보정은 별도 판단이다 — 회전은 보간이라 획을 흐리게
    만들고, 기울기가 1도 미만이면 잃는 것이 더 많다.

    방법 — 후보 각도로 회전해 보고 **행 합(수평 투영)의 분산이 최대**인
    각도를 고른다. 괘선이 수평에 맞으면 그 행에 검은 픽셀이 몰려 분산이
    커진다. 표 문서에 잘 맞는 고전적 방법이고 Hough 보다 잡음에 둔하다.
    """
    a = as_gray(png)
    # 계산량을 줄인다 — 기울기는 축소해도 보인다
    h, w = a.shape
    k = max(1, min(h // 800, w // 600, 4))
    small = a[::k, ::k]
    ink = (small < 128).astype(np.float32)

    best, best_score = 0.0, -1.0
    n = int(limit / step)
    for i in range(-n, n + 1):
        ang = i * step
        rot = ink if ang == 0.0 else np.asarray(
            Image.fromarray((ink * 255).astype(np.uint8)).rotate(
                ang, resample=Image.BILINEAR, fillcolor=0),
            dtype=np.float32) / 255.0
        prof = rot.sum(axis=1)
        score = float(np.var(prof))
        if score > best_score:
            best, best_score = ang, score
    return best


# ── 펀치 구멍 ───────────────────────────────────────────────────

def remove_punch_holes(png: str, out: str | None = None,
                       margin: float = 0.10, min_frac: float = 0.010,
                       max_frac: float = 0.055) -> tuple[str, int]:
    """좌·우 여백의 펀치 구멍을 흰색으로 채운다. → (경로, 채운 개수)

    1986년대 양식은 좌측 여백에 큰 검은 원이 찍혀 있다. 값과 무관한 잉크이고
    모델의 주의를 끌 이유가 없다.

    **여백에서만 찾는다.** 문서 가운데의 큰 검은 덩어리는 도장·서명일 수
    있고 그것은 값의 근거가 된다 — 지우면 안 된다.

    판별은 블록 밀도로 한다(`cv2` 없이). 8x8 블록이 거의 전부 검으면 후보,
    이어진 후보 덩어리가 **둥글고**(가로세로비 0.6~1.6) 크기가 여백 폭에
    비해 적당하면 구멍으로 본다.
    """
    a = as_gray(png)
    h, w = a.shape
    b = 8
    hh, ww = h // b, w // b
    dark = (a[:hh * b, :ww * b].reshape(hh, b, ww, b).mean(axis=(1, 3)) < 60)

    edge = max(1, int(ww * margin))
    band = np.zeros_like(dark)
    band[:, :edge] = dark[:, :edge]
    band[:, -edge:] = dark[:, -edge:]

    # 이어진 덩어리 찾기 — 반복 팽창으로 라벨링 대신 씨앗 확장
    seen = np.zeros_like(band, dtype=bool)
    boxes = []
    for y in range(hh):
        for x in range(ww):
            if not band[y, x] or seen[y, x]:
                continue
            stack, cells = [(y, x)], []
            seen[y, x] = True
            while stack:
                cy, cx = stack.pop()
                cells.append((cy, cx))
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = cy + dy, cx + dx
                    if (0 <= ny < hh and 0 <= nx < ww
                            and band[ny, nx] and not seen[ny, nx]):
                        seen[ny, nx] = True
                        stack.append((ny, nx))
            ys = [c[0] for c in cells]
            xs = [c[1] for c in cells]
            bh, bw = max(ys) - min(ys) + 1, max(xs) - min(xs) + 1
            if bh == 0 or bw == 0:
                continue
            ratio = bw / bh
            size = max(bh, bw) / hh
            fill = len(cells) / (bh * bw)
            if 0.6 <= ratio <= 1.6 and min_frac <= size <= max_frac and fill > 0.6:
                boxes.append((min(ys) * b, min(xs) * b,
                              (max(ys) + 1) * b, (max(xs) + 1) * b))

    if not boxes:
        return png, 0

    with Image.open(png) as im:
        im = im.convert("L")
        arr = np.asarray(im).copy()
        pad = b
        for y0, x0, y1, x1 in boxes:
            arr[max(0, y0 - pad):min(h, y1 + pad),
                max(0, x0 - pad):min(w, x1 + pad)] = 255
        out = out or png.replace(".png", "_nh.png")
        Image.fromarray(arr).save(out, "PNG")
    return out, len(boxes)


# ── 확대 ────────────────────────────────────────────────────────

def upscale(png: str, k: float, out: str | None = None) -> str:
    """보간 확대. tif 는 1비트라 DPI 로는 안 커지므로 여기서 키운다.

    1비트는 보간 전에 회색조로 올린다 — 안 그러면 계단이 그대로 남는다.
    """
    if k <= 1.0:
        return png
    out = out or png.replace(".png", f"_x{k:g}.png")
    if os.path.exists(out):
        return out
    with Image.open(png) as im:
        w, h = im.size
        im = im.convert("L") if im.mode == "1" else im
        im.resize((int(w * k), int(h * k)), Image.LANCZOS).save(out, "PNG")
    return out


# ── 장변 맞추기 ─────────────────────────────────────────────────

def resize_long_edge(png: str, edge: int, out: str | None = None) -> str:
    """장변을 목표 픽셀에 맞춘다. **확대·축소 양방향.**

    `upscale()` 은 배율을 받아 **확대만** 한다. 이 함수는 목표 장변을 받아
    원본이 크면 줄이고 작으면 키운다 — 포맷이 섞인 코퍼스에서 모델에 가는
    이미지 크기를 일정하게 만들기 위한 것이다.

    왜 배율로는 안 되는가 (2026-08-26 실측)
    ─────────────────────────────────────────────────────────
        tif   748건 중 99.6% 가 1240x1753   (A4 150dpi)
        pdf   181건 · 페이지 1,120장의 렌더 장변이 **1500 ~ 7306px**
              최대는 `20FV904-DATA SHEET_REV0.pdf` 의 5168x7306 (A0 를 150dpi)

    배율 1.47 을 일괄로 주면 tif 는 2576 이 되지만 A0 도면은 10,740 이 된다.
    그 크기는 패치 37,098 개 · 이미지 토큰 44,518 로 tif 한 장(2,574)의 **17배**이고,
    비용과 모델 상한 양쪽에 걸린다. 장변 목표로 주면 그런 문서가 **줄어든다.**

    보간법
        축소·확대 모두 LANCZOS 를 쓴다. `upscale()` 과 같은 선택을 유지해
        두 경로가 서로 다른 결과를 내지 않게 한다 — 같은 소스에서 같은 크기로
        가면 두 함수의 출력이 픽셀 단위로 동일해야 A/B 가 성립한다.

    입력  : png — 원본 PNG 경로, edge — 목표 장변(px), out — 출력 경로(선택)
    출력  : 조정된 PNG 경로. 이미 목표 크기면 **입력 경로를 그대로 돌려준다**
            (파일을 쓸데없이 늘리지 않는다)
    부수효과: 파일 쓰기. 같은 이름이 이미 있으면 재사용한다
    """
    with Image.open(png) as im:
        w, h = im.size                                  # 원본 크기
        cur = max(w, h)                                 # 현재 장변
        if cur == edge or cur == 0:                     # 이미 목표거나 빈 이미지면 손대지 않는다
            return png
        k = edge / cur                                  # 확대(>1) 또는 축소(<1) 배율
        out = out or png.replace(".png", f"_le{edge}.png")
        if os.path.exists(out):                         # 같은 조건으로 이미 만들어 뒀으면 재사용
            return out
        # 1비트는 보간 전에 회색조로 올린다 — 안 그러면 계단이 그대로 남는다.
        im = im.convert("L") if im.mode == "1" else im
        # 반올림이 아니라 int() 를 쓴다 — `upscale()` 과 같은 규칙이라야
        # 같은 소스·같은 목표에서 두 함수가 픽셀 단위로 같은 결과를 낸다.
        im.resize((max(int(w * k), 1), max(int(h * k), 1)), Image.LANCZOS).save(out, "PNG")
    return out
