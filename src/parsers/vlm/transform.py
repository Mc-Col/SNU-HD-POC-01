# -*- coding: utf-8 -*-
"""좌표 변환 체인 — 전처리로 움직인 좌표를 원본 페이지 좌표로 되돌린다.

왜 필요한가
    VLM 은 "전처리된 이미지"를 보고 bbox 를 돌려준다. 그런데 계약
    (`RawExtraction.bbox` = 페이지 좌표)과 UI 하이라이트는 "원본 페이지" 기준이다.
    회전·확대축소를 기록해 두고 역변환하지 않으면 하이라이트가 어긋나 bbox 계약이 깨진다.

좌표 규약
    픽셀 중심 기준(coordinate = pixel_index + 0.5)을 쓴다.
    이 규약을 쓰면 90° 회전과 확대축소의 수식이 오프셋 없이 정확히 맞는다.
    PIL 의 `Image.transform` 은 픽셀 인덱스 기준이므로, 넘길 때만 반 픽셀을 보정한다.
"""
from __future__ import annotations                      # 타입 표기 일관성 유지

import math                                             # 삼각함수·ceil
from dataclasses import dataclass                       # 변환 기록을 불변 자료구조로

# 2x3 아핀 행렬을 (a, b, c, d, e, f) 로 표현한다.
#   x' = a*x + b*y + c
#   y' = d*x + e*y + f
Matrix = tuple[float, float, float, float, float, float]

# 항등 변환. 아무것도 하지 않았을 때의 기준값.
IDENTITY: Matrix = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)


def compose(second: Matrix, first: Matrix) -> Matrix:
    """두 아핀 변환을 합성한다 (first 를 적용한 뒤 second 를 적용).

    입력  : second — 나중에 적용할 행렬, first — 먼저 적용할 행렬
    출력  : 합성 행렬
    부수효과: 없음
    """
    a1, b1, c1, d1, e1, f1 = first                      # 먼저 적용할 변환의 성분
    a2, b2, c2, d2, e2, f2 = second                     # 나중에 적용할 변환의 성분
    return (
        a2 * a1 + b2 * d1,                              # 새 a = 2행렬 1행 · 1행렬 1열
        a2 * b1 + b2 * e1,                              # 새 b
        a2 * c1 + b2 * f1 + c2,                         # 새 c (평행이동 누적)
        d2 * a1 + e2 * d1,                              # 새 d
        d2 * b1 + e2 * e1,                              # 새 e
        d2 * c1 + e2 * f1 + f2,                          # 새 f (평행이동 누적)
    )


def invert(matrix: Matrix) -> Matrix:
    """아핀 변환의 역행렬을 구한다.

    입력  : matrix — 정변환 행렬
    출력  : 역변환 행렬
    부수효과: 없음. 행렬식이 0 이면 ValueError (조용히 넘기지 않는다).
    """
    a, b, c, d, e, f = matrix                           # 성분 분해
    det = a * e - b * d                                 # 2x2 부분의 행렬식
    if abs(det) < 1e-12:                                # 특이행렬이면 역변환이 정의되지 않는다
        raise ValueError(f"역변환할 수 없는 변환 행렬(det≈0): {matrix!r}")
    return (
        e / det,                                        # 역행렬 a'
        -b / det,                                       # 역행렬 b'
        (b * f - c * e) / det,                          # 역행렬 c'
        -d / det,                                       # 역행렬 d'
        a / det,                                        # 역행렬 e'
        (c * d - a * f) / det,                          # 역행렬 f'
    )


def apply_point(matrix: Matrix, x: float, y: float) -> tuple[float, float]:
    """점 하나에 아핀 변환을 적용한다.

    입력  : matrix — 변환 행렬, x/y — 좌표(픽셀 중심 규약)
    출력  : 변환된 (x, y)
    부수효과: 없음
    """
    a, b, c, d, e, f = matrix                           # 성분 분해
    return (a * x + b * y + c, d * x + e * y + f)       # 두 성분을 각각 계산


def apply_bbox(
    matrix: Matrix, bbox: tuple[float, float, float, float]
) -> tuple[float, float, float, float]:
    """축 정렬 bbox 에 아핀 변환을 적용한다.

    역할  : 네 꼭짓점을 변환한 뒤 그 외접 사각형을 돌려준다.
            (회전이 섞이면 bbox 는 축 정렬을 유지할 수 없으므로 외접 사각형이 최선이다.
             기울기 보정 각도가 ±5° 이내라 팽창은 미미하다.)
    입력  : matrix — 변환 행렬, bbox — (x0, y0, x1, y1)
    출력  : 변환된 (x0, y0, x1, y1)
    부수효과: 없음
    """
    x0, y0, x1, y1 = bbox                               # 입력 사각형 분해
    corners = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))  # 네 꼭짓점
    moved = [apply_point(matrix, cx, cy) for cx, cy in corners]  # 각각 변환
    xs = [p[0] for p in moved]                          # 변환된 x 좌표들
    ys = [p[1] for p in moved]                          # 변환된 y 좌표들
    return (min(xs), min(ys), max(xs), max(ys))         # 외접 사각형


def rotation_matrix_with_expand(
    width: int, height: int, degrees_ccw: float
) -> tuple[Matrix, int, int]:
    """반시계 회전 + 캔버스 확장(내용 잘림 없음)의 정변환 행렬과 출력 크기를 구한다.

    역할  : 회전으로 이미지가 잘리지 않도록 캔버스를 키우고, 그때의 좌표 대응을 계산한다.
    입력  : width/height — 원본 크기, degrees_ccw — 반시계 회전 각도(도)
    출력  : (정변환 행렬, 새 너비, 새 높이)
    부수효과: 없음
    """
    theta = math.radians(degrees_ccw)                   # 도 → 라디안
    cos_t = math.cos(theta)                             # 코사인 (반복 계산 피하기)
    sin_t = math.sin(theta)                             # 사인
    # 화면 좌표계(y 아래 방향)에서 "시각적 반시계" 회전은 아래 행렬이 된다.
    #   x' =  cos*x + sin*y ,  y' = -sin*x + cos*y
    rotate_only: Matrix = (cos_t, sin_t, 0.0, -sin_t, cos_t, 0.0)
    corners = ((0.0, 0.0), (float(width), 0.0), (float(width), float(height)), (0.0, float(height)))
    moved = [apply_point(rotate_only, cx, cy) for cx, cy in corners]  # 원점 회전 후의 꼭짓점
    min_x = min(p[0] for p in moved)                    # 왼쪽으로 밀려난 양
    min_y = min(p[1] for p in moved)                    # 위로 밀려난 양
    max_x = max(p[0] for p in moved)                    # 오른쪽 끝
    max_y = max(p[1] for p in moved)                    # 아래쪽 끝
    new_w = int(math.ceil(max_x - min_x - 1e-9))        # 새 너비(부동소수 오차를 먼저 깎고 올림)
    new_h = int(math.ceil(max_y - min_y - 1e-9))        # 새 높이
    # 음수 좌표가 생기지 않도록 평행이동을 더해 최종 정변환을 만든다.
    matrix: Matrix = (cos_t, sin_t, -min_x, -sin_t, cos_t, -min_y)
    return matrix, max(new_w, 1), max(new_h, 1)         # 최소 1px 은 보장한다


def scale_matrix(scale_x: float, scale_y: float) -> Matrix:
    """확대축소의 정변환 행렬. 픽셀 중심 규약에서는 평행이동 항이 0 이다."""
    return (scale_x, 0.0, 0.0, 0.0, scale_y, 0.0)       # x' = sx*x, y' = sy*y


def to_pil_affine_coeffs(inverse_matrix: Matrix) -> tuple[float, ...]:
    """역변환 행렬을 PIL `Image.transform(..., AFFINE, coeffs)` 인자로 바꾼다.

    역할  : PIL 은 "출력 픽셀 인덱스 → 입력 좌표" 를 요구한다. 우리 행렬은
            픽셀 중심 규약이므로 반 픽셀 차이를 상수항에 흡수시킨다.
              src_index + 0.5 = M(dst_index + 0.5)  →  c' = 0.5a + 0.5b + c - 0.5
    입력  : inverse_matrix — 전처리 좌표 → 원본 좌표 행렬
    출력  : PIL 이 요구하는 6개 계수
    부수효과: 없음
    """
    a, b, c, d, e, f = inverse_matrix                   # 성분 분해
    return (
        a, b, 0.5 * a + 0.5 * b + c - 0.5,              # x 성분 (상수항에 반 픽셀 보정)
        d, e, 0.5 * d + 0.5 * e + f - 0.5,              # y 성분 (동일)
    )


@dataclass(frozen=True)
class PageTransform:
    """원본 페이지 ↔ 전처리 이미지의 좌표 대응 기록.

    역할  : 전처리에서 무엇을 했는지(방향·기울기·배율)와 그 좌표 변환을 함께 보관한다.
    입력  : 없음 (전처리기가 생성한다)
    출력  : 좌표/bbox 변환 메서드
    부수효과: 없음 (불변 객체)
    """
    original_size: tuple[int, int]                      # (너비, 높이) — 원본 페이지 이미지 크기
    processed_size: tuple[int, int]                      # (너비, 높이) — 전처리 결과 크기
    orientation_degrees: int                             # 적용한 90° 단위 반시계 회전 (0 또는 90)
    deskew_degrees: float                                # 적용한 미세 반시계 회전 (적용 안 했으면 0.0)
    scale: float                                         # 적용한 배율 (1.0 이면 그대로)
    forward: Matrix                                      # 원본 → 전처리
    inverse: Matrix                                      # 전처리 → 원본

    @classmethod
    def identity(cls, size: tuple[int, int]) -> "PageTransform":
        """아무 전처리도 하지 않은 경우의 변환 기록을 만든다."""
        return cls(
            original_size=size,                          # 원본 크기
            processed_size=size,                         # 그대로
            orientation_degrees=0,                       # 방향 보정 없음
            deskew_degrees=0.0,                          # 기울기 보정 없음
            scale=1.0,                                   # 배율 없음
            forward=IDENTITY,                            # 항등
            inverse=IDENTITY,                            # 항등
        )

    @classmethod
    def build(
        cls,
        original_size: tuple[int, int],
        processed_size: tuple[int, int],
        *,
        orientation_degrees: int,
        deskew_degrees: float,
        scale: float,
        forward: Matrix,
    ) -> "PageTransform":
        """정변환 행렬로부터 역변환을 계산해 기록을 완성한다."""
        return cls(
            original_size=original_size,                 # 원본 크기
            processed_size=processed_size,               # 결과 크기
            orientation_degrees=orientation_degrees,     # 방향 보정량
            deskew_degrees=deskew_degrees,               # 기울기 보정량
            scale=scale,                                 # 배율
            forward=forward,                             # 정변환
            inverse=invert(forward),                     # 역변환 (여기서 한 번만 계산)
        )

    def point_to_processed(self, x: float, y: float) -> tuple[float, float]:
        """원본 좌표 → 전처리 좌표."""
        return apply_point(self.forward, x, y)           # 정변환 적용

    def point_to_original(self, x: float, y: float) -> tuple[float, float]:
        """전처리 좌표 → 원본 좌표."""
        return apply_point(self.inverse, x, y)           # 역변환 적용

    def bbox_to_processed(
        self, bbox: tuple[float, float, float, float]
    ) -> tuple[float, float, float, float]:
        """원본 bbox → 전처리 bbox."""
        return apply_bbox(self.forward, bbox)            # 정변환 적용

    def bbox_to_original(
        self, bbox: tuple[float, float, float, float]
    ) -> tuple[float, float, float, float]:
        """전처리 bbox → 원본 bbox. UI 하이라이트가 쓰는 값이다."""
        return apply_bbox(self.inverse, bbox)            # 역변환 적용

    def norm_bbox_to_original(
        self, norm_bbox: tuple[float, float, float, float]
    ) -> tuple[float, float, float, float]:
        """VLM 이 준 정규화 bbox(0~1, 전처리 이미지 기준) → 원본 픽셀 bbox.

        역할  : 모델에게는 해상도와 무관한 0~1 좌표를 요구하고(그게 더 안정적이다),
                받은 값을 전처리 픽셀 → 원본 픽셀로 두 단계 되돌린다.
        입력  : norm_bbox — (x0, y0, x1, y1), 각 0.0~1.0
        출력  : 원본 페이지 픽셀 bbox
        부수효과: 없음
        """
        proc_w, proc_h = self.processed_size             # 전처리 이미지 크기
        x0, y0, x1, y1 = norm_bbox                       # 정규화 좌표 분해
        pixel_bbox = (                                   # 정규화 → 전처리 픽셀
            x0 * proc_w,                                 # 좌
            y0 * proc_h,                                 # 상
            x1 * proc_w,                                 # 우
            y1 * proc_h,                                 # 하
        )
        return self.bbox_to_original(pixel_bbox)         # 전처리 픽셀 → 원본 픽셀

    def clamp_to_original(
        self, bbox: tuple[float, float, float, float]
    ) -> tuple[float, float, float, float]:
        """bbox 를 원본 페이지 경계 안으로 자른다.

        역할  : 회전 역변환 뒤 좌표가 페이지를 살짝 벗어날 수 있어 UI 가 깨지는 것을 막는다.
        입력  : bbox — 원본 좌표계 bbox
        출력  : 경계 안으로 잘린 bbox (좌상 ≤ 우하 보장)
        부수효과: 없음
        """
        width, height = self.original_size               # 원본 크기
        x0, y0, x1, y1 = bbox                            # 분해
        cx0 = min(max(x0, 0.0), float(width))            # 좌를 [0, W] 로 제한
        cy0 = min(max(y0, 0.0), float(height))           # 상을 [0, H] 로 제한
        cx1 = min(max(x1, 0.0), float(width))            # 우를 [0, W] 로 제한
        cy1 = min(max(y1, 0.0), float(height))           # 하를 [0, H] 로 제한
        return (min(cx0, cx1), min(cy0, cy1), max(cx0, cx1), max(cy0, cy1))  # 순서 정규화
