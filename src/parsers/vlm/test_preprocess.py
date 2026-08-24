# -*- coding: utf-8 -*-
"""전처리 테스트 — 합성 열화 이미지로 API 없이 단독 검증한다.

케이스: 정상 / 3도 기울어짐 / 임계 이하 미세 기울기 / 90도 회전 / 고해상도 / 저해상도
"""
from __future__ import annotations                      # 타입 표기 일관성 유지

import numpy as np                                      # 잉크 무게중심 계산
import pytest                                           # 테스트 프레임워크

from src.parsers.vlm.constants import (                 # 임계값 (테스트도 상수를 참조한다)
    DESKEW_MIN_APPLY_DEGREES,
    MAX_UPSCALE_FACTOR,
    TARGET_LONG_EDGE_PX,
)
from src.parsers.vlm.preprocess import (                # 검증 대상
    estimate_orientation,
    estimate_skew,
    plan_scale,
    preprocess,
)


def _ink_centroid(image) -> tuple[float, float]:
    """이미지의 잉크(어두운 화소) 무게중심을 구한다.

    역할  : 전처리가 픽셀을 옮긴 방향과 좌표 변환 기록이 일치하는지 확인하는 독립 지표다.
            회전·확대축소는 내용 전체를 같은 규칙으로 옮기므로 무게중심도 같은 규칙으로 옮겨진다.
    입력  : image — PIL 이미지
    출력  : (x, y) 픽셀 중심 규약 좌표
    부수효과: 없음
    """
    array = np.asarray(image.convert("L"), dtype=np.float32)   # 흑백 배열
    ink = np.clip(255.0 - array, 0.0, None)                    # 잉크가 큰 값이 되도록 반전
    total = float(ink.sum())                                   # 총 잉크량
    assert total > 0, "잉크가 없는 이미지로는 무게중심을 구할 수 없다"
    ys, xs = np.indices(ink.shape)                             # 각 화소의 좌표 격자
    cx = float((ink * (xs + 0.5)).sum() / total)               # x 무게중심 (픽셀 중심 규약)
    cy = float((ink * (ys + 0.5)).sum() / total)               # y 무게중심
    return cx, cy                                              # 무게중심


# ══════════════════════════════════════════════════════════════════
#  ① 기울기 보정
# ══════════════════════════════════════════════════════════════════

def test_clean_page_is_not_rotated(load_fixture_image):
    """정상 페이지는 기울기 보정을 하지 않는다."""
    image = load_fixture_image("clean")                        # 기준 이미지
    result = preprocess(image)                                  # 전처리
    assert result.transform.deskew_degrees == 0.0               # 회전 적용 없음
    assert result.transform.orientation_degrees == 0            # 방향 보정 없음


def test_skew3_is_detected_and_corrected(load_fixture_image):
    """3도 기울어진 페이지의 보정 각도를 0.4도 이내로 추정하고 실제로 회전한다."""
    image = load_fixture_image("skew3")                         # 시계 3도로 기울인 이미지
    estimated = estimate_skew(image)                            # 추정 각도 (반시계 보정량)
    assert estimated == pytest.approx(3.0, abs=0.4)             # +3도 근처여야 한다
    result = preprocess(image)                                  # 전처리 적용
    assert result.transform.deskew_degrees == pytest.approx(3.0, abs=0.4)  # 적용 각도 기록
    assert any("기울기 보정" in note for note in result.notes)    # 관찰 메모에 남는다


def test_micro_skew_below_threshold_is_not_rotated(load_fixture_image):
    """0.5도 이하의 미세 각도는 재샘플링 손실이 커서 회전하지 않는다."""
    image = load_fixture_image("micro_skew")                    # 0.3도 기울인 이미지
    estimated = estimate_skew(image)                            # 추정 각도
    assert abs(estimated) <= DESKEW_MIN_APPLY_DEGREES           # 임계 이하로 추정되어야 한다
    result = preprocess(image)                                  # 전처리
    assert result.transform.deskew_degrees == 0.0               # 회전은 적용하지 않는다
    assert result.image.size == image.size                      # 크기도 그대로 (재샘플링 없음)
    assert any("미적용" in note for note in result.notes)         # 추정했지만 미적용임을 남긴다


# ══════════════════════════════════════════════════════════════════
#  ② 90도 방향 보정
# ══════════════════════════════════════════════════════════════════

def test_rot90_is_detected(load_fixture_image):
    """누운 페이지는 반시계 90도 보정 대상으로 판정된다."""
    image = load_fixture_image("rot90")                         # 시계 90도로 눕힌 이미지
    assert estimate_orientation(image) == 90                    # 90도 보정 판정
    result = preprocess(image)                                  # 전처리
    assert result.transform.orientation_degrees == 90           # 적용 기록
    # 가로로 누웠던 페이지가 세로로 선다 (원본과 가로세로가 뒤바뀐다).
    assert result.image.height > result.image.width


def test_upright_page_is_not_reoriented(load_fixture_image):
    """이미 바로 선 페이지는 방향을 건드리지 않는다."""
    image = load_fixture_image("clean")                         # 정상 이미지
    assert estimate_orientation(image) == 0                     # 0도 판정
    result = preprocess(image)                                  # 전처리
    assert result.transform.orientation_degrees == 0            # 적용 없음


# ══════════════════════════════════════════════════════════════════
#  ③ 해상도 정규화
# ══════════════════════════════════════════════════════════════════

def test_highres_is_downscaled_to_target(load_fixture_image):
    """장변이 2576 을 넘으면 정확히 목표 장변으로 축소한다."""
    image = load_fixture_image("highres")                       # 2480x3508
    assert max(image.size) > TARGET_LONG_EDGE_PX                # 전제 확인
    result = preprocess(image)                                  # 전처리
    assert max(result.image.size) == TARGET_LONG_EDGE_PX        # 목표 장변에 정확히 맞춘다
    assert result.transform.scale < 1.0                         # 축소가 기록된다


def test_lowres_is_upscaled_at_most_twice(load_fixture_image):
    """목표의 절반 이하로 작으면 최대 2배까지만 확대한다."""
    image = load_fixture_image("lowres")                        # 620x877
    result = preprocess(image)                                  # 전처리
    assert result.transform.scale == pytest.approx(MAX_UPSCALE_FACTOR)  # 정확히 2배
    assert max(result.image.size) == max(image.size) * 2        # 실제 크기도 2배
    assert max(result.image.size) < TARGET_LONG_EDGE_PX         # 2배로도 목표에 못 미친다(=상한이 작동)


def test_mid_resolution_is_left_alone(load_fixture_image):
    """목표의 절반~목표 사이 해상도는 손대지 않는다 (불필요한 재샘플링 금지)."""
    image = load_fixture_image("clean")                         # 장변 1754 (1288 < 1754 < 2576)
    assert plan_scale(image.size) == 1.0                        # 배율 계획이 1.0
    result = preprocess(image)                                  # 전처리
    assert result.image.size == image.size                      # 크기 변화 없음


# ══════════════════════════════════════════════════════════════════
#  ④ 좌표 변환 체인이 실제 픽셀 이동과 일치하는가
# ══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("name", ["clean", "skew3", "rot90", "highres", "lowres"])
def test_transform_matches_actual_pixel_motion(load_fixture_image, name):
    """기록된 정변환으로 원본 잉크 무게중심을 옮기면 전처리 이미지의 무게중심과 맞는다.

    이것이 좌표 역변환 체인의 end-to-end 검증이다. 픽셀은 A 로 옮기고 좌표는 B 로
    계산하는 불일치가 있으면 여기서 바로 드러난다.
    """
    image = load_fixture_image(name)                            # 대상 이미지
    result = preprocess(image)                                  # 전처리

    original_centroid = _ink_centroid(result.original)          # 원본 잉크 무게중심
    processed_centroid = _ink_centroid(result.image)             # 전처리 이미지의 무게중심
    predicted = result.transform.point_to_processed(*original_centroid)  # 기록된 변환으로 예측

    # 재샘플링(BICUBIC/LANCZOS)과 회전 배경 채움 때문에 완전히 같지는 않다.
    # 페이지 장변의 0.6% 이내면 좌표계가 일치한다고 볼 수 있다.
    tolerance = max(result.image.size) * 0.006
    assert predicted[0] == pytest.approx(processed_centroid[0], abs=tolerance)
    assert predicted[1] == pytest.approx(processed_centroid[1], abs=tolerance)

    # 역변환으로 되돌리면 원본 무게중심으로 돌아온다 (수식 왕복 확인).
    back = result.transform.point_to_original(*predicted)
    assert back[0] == pytest.approx(original_centroid[0], abs=1e-6)
    assert back[1] == pytest.approx(original_centroid[1], abs=1e-6)


def test_preprocess_keeps_original_untouched(load_fixture_image):
    """전처리는 입력 이미지를 변형하지 않고 원본을 그대로 보관한다."""
    image = load_fixture_image("skew3")                         # 회전이 일어나는 케이스
    before = image.size                                          # 원래 크기
    result = preprocess(image)                                   # 전처리
    assert image.size == before                                  # 입력 객체는 그대로
    assert result.original.size == before                        # 원본 보관본도 같은 크기
    assert result.image.size != before                           # 결과는 회전으로 커졌다


def test_preprocess_accepts_path(tmp_path, load_fixture_image):
    """파일 경로를 그대로 넘겨도 동작한다 (pipeline 편의)."""
    image = load_fixture_image("clean")                          # 이미지 준비
    path = tmp_path / "page.png"                                 # 임시 파일
    image.save(path)                                             # 저장
    result = preprocess(path)                                    # 경로로 호출
    assert result.image.size == image.size                       # 같은 결과


def test_preprocess_is_deterministic(load_fixture_image):
    """같은 입력 → 같은 출력 (개발 철학 6)."""
    image = load_fixture_image("skew3")                          # 회전·판정이 모두 일어나는 케이스
    first = preprocess(image)                                     # 1회
    second = preprocess(image)                                    # 2회
    assert first.transform == second.transform                    # 변환 기록이 완전히 같다
    assert first.image.tobytes() == second.image.tobytes()        # 픽셀도 완전히 같다
