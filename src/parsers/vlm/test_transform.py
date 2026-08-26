# -*- coding: utf-8 -*-
"""좌표 역변환 단위 테스트 — 정변환 → 역변환이 원위치로 돌아오는지.

이 테스트가 지키는 계약
    `RawExtraction.bbox` 는 원본 페이지 좌표다. 전처리에서 회전·확대축소를 하고도
    좌표를 되돌리지 못하면 UI 하이라이트가 어긋나 bbox 계약이 깨진다.
"""
from __future__ import annotations                      # 타입 표기 일관성 유지

import math                                             # 각도 계산

import pytest                                           # 테스트 프레임워크

from src.parsers.vlm.transform import (                 # 검증 대상
    IDENTITY,
    PageTransform,
    apply_bbox,
    apply_point,
    compose,
    invert,
    rotation_matrix_with_expand,
    scale_matrix,
)

# 왕복 오차 허용치. 부동소수 연산만 하므로 아주 작아야 한다.
TOLERANCE = 1e-6


def test_invert_identity_is_identity():
    """항등 변환의 역변환은 항등이다."""
    inverted = invert(IDENTITY)                         # 역변환 계산
    for got, expected in zip(inverted, IDENTITY):       # 성분별 비교
        assert got == pytest.approx(expected, abs=TOLERANCE)


def test_invert_rejects_singular_matrix():
    """특이행렬은 조용히 넘기지 않고 예외를 던진다."""
    with pytest.raises(ValueError):                     # 배율 0 은 역변환이 불가능하다
        invert(scale_matrix(0.0, 1.0))


@pytest.mark.parametrize("degrees", [0.0, 0.5, 3.0, -3.0, 5.0, -5.0, 90.0])
def test_rotation_roundtrip(degrees):
    """회전 정변환 후 역변환하면 원래 점으로 돌아온다."""
    width, height = 1240, 1754                          # 페이지 크기
    matrix, new_w, new_h = rotation_matrix_with_expand(width, height, degrees)  # 정변환
    inverse = invert(matrix)                            # 역변환
    for x, y in ((0.0, 0.0), (620.0, 877.0), (1239.0, 1753.0), (100.5, 42.25)):
        moved = apply_point(matrix, x, y)               # 정변환 적용
        assert -1.0 <= moved[0] <= new_w + 1.0          # 확장 캔버스 안에 들어와야 한다
        assert -1.0 <= moved[1] <= new_h + 1.0          # (경계 반올림 여유 1px)
        back = apply_point(inverse, *moved)             # 역변환 적용
        assert back[0] == pytest.approx(x, abs=TOLERANCE)  # x 가 원위치
        assert back[1] == pytest.approx(y, abs=TOLERANCE)  # y 가 원위치


def test_rotation_90_matches_pil_transpose_geometry():
    """90도 회전의 좌표 수식이 PIL transpose 의 실제 픽셀 이동과 맞는다."""
    from PIL import Image                               # 실제 픽셀 이동을 확인하려면 PIL 이 필요

    width, height = 40, 25                              # 작은 이미지로 픽셀 단위 확인
    image = Image.new("L", (width, height), 0)          # 검은 바탕
    marker_x, marker_y = 33, 4                          # 표식 픽셀 위치 (오른쪽 위)
    image.putpixel((marker_x, marker_y), 255)          # 흰 점 하나

    turned = image.transpose(Image.ROTATE_90)          # PIL 의 반시계 90도
    matrix, new_w, new_h = rotation_matrix_with_expand(width, height, 90.0)  # 우리 수식
    assert (turned.width, turned.height) == (new_w, new_h)  # 크기 계산이 일치해야 한다

    # 픽셀 중심 규약(index + 0.5)으로 정변환한 뒤 다시 인덱스로 되돌린다.
    px, py = apply_point(matrix, marker_x + 0.5, marker_y + 0.5)
    predicted = (int(math.floor(px)), int(math.floor(py)))                 # 예측 위치
    found = [                                                              # 실제 흰 점 위치
        (x, y)
        for y in range(turned.height)
        for x in range(turned.width)
        if turned.getpixel((x, y)) > 128
    ]
    assert found == [predicted]                        # 예측과 실제가 정확히 같아야 한다


def test_composed_chain_roundtrip():
    """방향 90도 → 기울기 3.7도 → 배율 0.734 를 합성해도 왕복이 성립한다."""
    width, height = 1240, 1754                          # 원본 크기
    m1, w1, h1 = rotation_matrix_with_expand(width, height, 90.0)   # ① 방향 보정
    m2, w2, h2 = rotation_matrix_with_expand(w1, h1, 3.7)           # ② 기울기 보정
    scale = 0.734                                                   # ③ 해상도 정규화 배율
    m3 = scale_matrix(scale, scale)                                 # 배율 행렬
    forward = compose(m3, compose(m2, m1))                          # 정변환 합성 (①→②→③)

    transform = PageTransform.build(                    # 변환 기록 생성
        original_size=(width, height),
        processed_size=(int(w2 * scale), int(h2 * scale)),
        orientation_degrees=90,
        deskew_degrees=3.7,
        scale=scale,
        forward=forward,
    )
    for x, y in ((0.0, 0.0), (17.0, 1200.0), (1239.5, 1753.5), (620.0, 877.0)):
        moved = transform.point_to_processed(x, y)      # 원본 → 전처리
        back = transform.point_to_original(*moved)      # 전처리 → 원본
        assert back[0] == pytest.approx(x, abs=TOLERANCE)  # 원위치 확인
        assert back[1] == pytest.approx(y, abs=TOLERANCE)


def test_bbox_roundtrip_contains_original():
    """bbox 왕복은 원래 사각형을 포함하고, 회전으로 인한 팽창이 과하지 않다."""
    width, height = 1240, 1754                          # 원본 크기
    m1, w1, h1 = rotation_matrix_with_expand(width, height, 3.0)    # 3도 기울기
    forward = compose(scale_matrix(0.8, 0.8), m1)                    # 배율까지 합성
    inverse = invert(forward)                                        # 역변환

    original_bbox = (200.0, 500.0, 640.0, 560.0)                     # 표 한 행 정도의 사각형
    processed = apply_bbox(forward, original_bbox)                    # 정변환
    back = apply_bbox(inverse, processed)                             # 역변환

    assert back[0] <= original_bbox[0] + TOLERANCE                    # 왼쪽을 잃지 않는다
    assert back[1] <= original_bbox[1] + TOLERANCE                    # 위를 잃지 않는다
    assert back[2] >= original_bbox[2] - TOLERANCE                    # 오른쪽을 잃지 않는다
    assert back[3] >= original_bbox[3] - TOLERANCE                    # 아래를 잃지 않는다

    # 3도 회전 두 번(정·역)의 축 정렬 팽창은 사각형 대각 길이에 비해 작아야 한다.
    grew_x = (back[2] - back[0]) - (original_bbox[2] - original_bbox[0])  # 가로 팽창량
    grew_y = (back[3] - back[1]) - (original_bbox[3] - original_bbox[1])  # 세로 팽창량
    assert grew_x < 30.0                                # 픽셀 기준 상한 (경험적)
    assert grew_y < 60.0                                # 세로는 회전 폭에 더 민감하다


def test_norm_bbox_to_original_maps_corners():
    """정규화 bbox(0~1) 가 원본 픽셀 좌표로 옳게 환산된다."""
    width, height = 1000, 2000                          # 원본 크기
    scale = 0.5                                          # 절반으로 축소
    forward = scale_matrix(scale, scale)                 # 배율만 적용한 경우
    transform = PageTransform.build(
        original_size=(width, height),
        processed_size=(int(width * scale), int(height * scale)),
        orientation_degrees=0,
        deskew_degrees=0.0,
        scale=scale,
        forward=forward,
    )
    # 전처리 이미지의 왼쪽 위 1/4 영역 → 원본에서도 왼쪽 위 1/4 영역이어야 한다.
    got = transform.norm_bbox_to_original((0.0, 0.0, 0.5, 0.5))
    assert got[0] == pytest.approx(0.0, abs=TOLERANCE)          # 좌
    assert got[1] == pytest.approx(0.0, abs=TOLERANCE)          # 상
    assert got[2] == pytest.approx(width * 0.5, abs=1.0)        # 우 (반올림 오차 1px 허용)
    assert got[3] == pytest.approx(height * 0.5, abs=1.0)       # 하


def test_clamp_to_original_keeps_bbox_inside_page():
    """역변환 결과가 페이지를 벗어나면 경계 안으로 잘린다."""
    transform = PageTransform.identity((800, 600))       # 800x600 페이지
    clamped = transform.clamp_to_original((-30.0, -10.0, 900.0, 700.0))  # 사방으로 벗어난 bbox
    assert clamped == (0.0, 0.0, 800.0, 600.0)          # 정확히 페이지 경계로 잘린다
