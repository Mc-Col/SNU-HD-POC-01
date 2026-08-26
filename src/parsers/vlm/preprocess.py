# -*- coding: utf-8 -*-
"""전처리 — 기울기 보정 · 90도 방향 보정 · 해상도 정규화. 그리고 좌표 변환 기록.

설계 문서가 명시한 전처리는 이 세 가지뿐이다.
품질 게이트·대비 보정·노이즈 제거 같은 확장은 여기 넣지 않는다(별도 실험장에서 검증).

opencv 를 쓰지 않는다. 각도 탐색과 회전은 pillow + numpy 로만 구현한다.
"""
from __future__ import annotations                      # 타입 표기 일관성 유지

import logging                                          # 관찰 사항을 조용히 버리지 않고 기록
from dataclasses import dataclass                       # 결과 묶음을 불변 자료구조로
from pathlib import Path                                # 경로 입력 지원

import numpy as np                                      # 투영 프로파일 계산 (분산·합)
from PIL import Image                                   # 이미지 회전·확대축소

from .constants import (                                # 임계값은 전부 상수 모듈에서 가져온다
    BACKGROUND_RGB,
    DESKEW_COARSE_STEP_DEGREES,
    DESKEW_FINE_STEP_DEGREES,
    DESKEW_FINE_WINDOW_DEGREES,
    DESKEW_MIN_APPLY_DEGREES,
    DESKEW_PROBE_LONG_EDGE,
    DESKEW_SCORE_WINDOW_RATIO,
    DESKEW_SEARCH_RANGE_DEGREES,
    MAX_UPSCALE_FACTOR,
    ORIENTATION_CANDIDATES,
    ORIENTATION_MARGIN_RATIO,
    TARGET_LONG_EDGE_PX,
    UPSCALE_TRIGGER_RATIO,
)
from .transform import (                                # 좌표 변환 체인
    IDENTITY,
    Matrix,
    PageTransform,
    compose,
    invert,
    rotation_matrix_with_expand,
    scale_matrix,
    to_pil_affine_coeffs,
)

logger = logging.getLogger(__name__)                    # 모듈 전용 로거

# 투영 프로파일 점수의 0 나눗셈 방지용 미세값.
_EPSILON: float = 1e-9


@dataclass(frozen=True)
class Preprocessed:
    """전처리 결과 묶음.

    역할  : VLM 에 넣을 이미지, 좌표 역변환 기록, 원본 이미지(크롭 재판독용),
            그리고 사람이 읽을 관찰 메모를 함께 들고 다닌다.
    입력  : 없음 (preprocess 가 생성)
    출력  : 불변 객체
    부수효과: 없음
    """
    image: Image.Image                                   # 전처리된 이미지 (API 로 보낼 것)
    transform: PageTransform                             # 좌표 역변환 기록 (bbox 계약 이행의 핵심)
    original: Image.Image                                # 원본 이미지 — 크롭 재판독은 여기서 잘라야 화질이 산다
    notes: tuple[str, ...] = ()                          # "3.1도 기울기 보정" 같은 관찰 메모

    @property
    def note_text(self) -> str:
        """관찰 메모를 RawExtraction.note 에 붙일 한 줄 문자열로 만든다."""
        return " | ".join(self.notes)                    # 계약의 note 관례(구분자 |)를 따른다


# ══════════════════════════════════════════════════════════════════
#  내부 유틸 — 점수 계산
# ══════════════════════════════════════════════════════════════════

def _to_rgb(image: Image.Image) -> Image.Image:
    """이미지를 RGB 로 맞춘다.

    역할  : 회전 시 흰 배경을 채우고 PNG 로 인코딩하려면 채널이 일정해야 한다.
            (색 보정이 아니라 자료형 통일이다.)
    입력  : PIL 이미지
    출력  : RGB 모드 이미지 (이미 RGB 면 원본 그대로)
    부수효과: 없음
    """
    if image.mode == "RGB":                             # 이미 RGB 면 복사 비용을 아낀다
        return image
    return image.convert("RGB")                         # 1/L/RGBA/P 등을 RGB 로 변환


def _make_probe(image: Image.Image) -> np.ndarray:
    """각도 탐색용 축소 흑백 배열을 만든다 (잉크가 큰 값).

    역할  : 원본 해상도로 수십 번 회전하면 느리다. 글줄의 굵은 구조만 보면 되므로 축소한다.
            잉크를 큰 값으로 반전해 두면 회전 시 빈 영역을 0 으로 채워도 점수가 왜곡되지 않는다.
    입력  : PIL 이미지
    출력  : float32 2차원 배열 (값이 클수록 잉크)
    부수효과: 없음
    """
    gray = image.convert("L")                           # 흑백 변환 (투영에는 밝기만 필요)
    long_edge = max(gray.size)                          # 현재 장변
    if long_edge > DESKEW_PROBE_LONG_EDGE:              # 목표보다 크면 축소한다
        ratio = DESKEW_PROBE_LONG_EDGE / float(long_edge)                 # 축소 비율
        new_size = (max(int(gray.width * ratio), 1), max(int(gray.height * ratio), 1))  # 최소 1px 보장
        gray = gray.resize(new_size, Image.LANCZOS)     # 축소는 LANCZOS (글줄 구조를 잘 보존)
    inverted = Image.eval(gray, lambda v: 255 - v)      # 밝기 반전 → 잉크가 큰 값이 된다
    return np.asarray(inverted, dtype=np.float32)       # numpy 배열로 (분산 계산용)


def _center_window(array: np.ndarray, ratio: float, square: bool) -> np.ndarray:
    """배열 중앙에서 일정 비율의 창을 잘라낸다.

    역할  : 회전마다 달라지는 모서리 결손을 점수에서 배제하고, 후보 간 창 크기를 같게 만든다.
    입력  : array — 2차원 배열, ratio — 창 비율, square — 정사각형으로 자를지 여부
    출력  : 잘린 2차원 배열
    부수효과: 없음
    """
    height, width = array.shape                         # 배열 크기
    if square:                                          # 방향(0 vs 90) 비교는 창 모양이 같아야 공평하다
        side = int(min(height, width) * ratio)          # 짧은 변 기준 정사각형
        win_h = win_w = max(side, 1)                    # 최소 1px 보장
    else:                                               # 같은 방향 안에서의 각도 비교는 비율 창으로 충분
        win_h = max(int(height * ratio), 1)             # 창 높이
        win_w = max(int(width * ratio), 1)              # 창 너비
    top = (height - win_h) // 2                         # 위쪽 여백
    left = (width - win_w) // 2                         # 왼쪽 여백
    return array[top:top + win_h, left:left + win_w]    # 중앙 창 반환


def _profile_score(window: np.ndarray) -> float:
    """행 방향 투영 프로파일의 정규화 분산을 계산한다.

    역할  : 글줄이 수평이면 "글자가 있는 행"과 "행간"의 차이가 커져 분산이 최대가 된다.
            잉크 총량 변화에 흔들리지 않도록 평균의 제곱으로 나눈다(변동계수의 제곱).
    입력  : window — 잉크가 큰 값인 2차원 배열
    출력  : 점수 (클수록 글줄이 수평)
    부수효과: 없음
    """
    profile = window.sum(axis=1)                        # 각 행의 잉크 총합 = 행 방향 투영
    mean = float(profile.mean())                        # 평균 잉크량
    if mean <= _EPSILON:                                # 완전 백지면 비교 의미가 없다
        return 0.0
    return float(profile.var()) / (mean * mean + _EPSILON)  # 정규화 분산


def _score_at_angle(probe: np.ndarray, degrees_ccw: float, *, square: bool) -> float:
    """탐침 배열을 주어진 각도로 회전한 뒤 투영 점수를 낸다.

    입력  : probe — 잉크가 큰 값인 배열, degrees_ccw — 반시계 각도, square — 정사각 창 사용 여부
    출력  : 점수
    부수효과: 없음
    """
    if degrees_ccw == 0.0:                              # 0도는 회전 자체를 건너뛴다 (불필요한 보간 방지)
        rotated = probe
    else:
        as_image = Image.fromarray(probe.astype(np.uint8))                    # 배열 → 이미지
        # expand=False 로 캔버스 크기를 고정한다. 후보마다 프로파일 길이가 달라지면 비교가 불공평해진다.
        # 빈 영역은 0(=잉크 없음)으로 채워야 반전 규약과 맞는다.
        turned = as_image.rotate(degrees_ccw, resample=Image.BILINEAR, expand=False, fillcolor=0)
        rotated = np.asarray(turned, dtype=np.float32)                       # 다시 배열로
    window = _center_window(rotated, DESKEW_SCORE_WINDOW_RATIO, square)      # 중앙 창만 평가
    return _profile_score(window)                                            # 점수 반환


# ══════════════════════════════════════════════════════════════════
#  ① 90도 단위 방향 보정
# ══════════════════════════════════════════════════════════════════

def estimate_orientation(image: Image.Image) -> int:
    """페이지가 누워 있는지 판정해 적용할 반시계 회전 각도를 돌려준다.

    역할  : 스캔 문서에는 가로로 누운 페이지가 흔하다. 글줄 투영이 가장 뚜렷한 방향을 고른다.
    입력  : image — 원본 이미지
    출력  : 0 또는 90 (반시계 회전 각도)
    부수효과: 없음

    한계 (설계상 의도된 것):
        행 방향 투영 점수는 180° 뒤집힘을 구분하지 못한다. 0° 와 180° 의 프로파일이
        (그리고 90° 와 270° 의 프로파일이) 사실상 동일하기 때문이다.
        그래서 180°/270° 는 아예 시도하지 않는다. 거꾸로 스캔된 페이지는 여기서
        바로잡히지 않고, VLM 이 글자를 읽지 못해 '판독 실패' 로 정직하게 남는다.
    """
    probe = _make_probe(image)                          # 축소 탐침 준비
    scores: dict[int, float] = {}                       # 후보별 점수
    for degrees in ORIENTATION_CANDIDATES:              # 0, 90 만 평가한다
        scores[degrees] = _score_at_angle(probe, float(degrees), square=True)  # 정사각 창으로 공평 비교
    base = scores.get(0, 0.0)                           # 회전하지 않았을 때의 점수
    best_degrees = 0                                    # 기본은 회전하지 않는 것
    best_score = base                                   # 기준 점수
    for degrees, score in scores.items():               # 후보를 훑는다
        if degrees == 0:                                # 기준값은 이미 반영했다
            continue
        if score > best_score and score > base * ORIENTATION_MARGIN_RATIO:  # 여유를 넘겨야 채택
            best_degrees = degrees                      # 채택된 회전량
            best_score = score                          # 채택된 점수
    logger.debug("방향 판정 점수=%s → %d도", scores, best_degrees)  # 판정 근거를 남긴다
    return best_degrees                                 # 0 또는 90


# ══════════════════════════════════════════════════════════════════
#  ② 기울기 보정 (projection profile, coarse → fine)
# ══════════════════════════════════════════════════════════════════

def estimate_skew(image: Image.Image) -> float:
    """행 방향 투영의 분산이 최대가 되는 보정 각도를 찾는다.

    역할  : ±5° 범위를 개략(1.0°) → 정밀(0.1°) 2단계로 탐색한다.
    입력  : image — (방향 보정이 끝난) 이미지
    출력  : 적용해야 할 반시계 보정 각도(도). 부호 그대로 회전하면 된다.
    부수효과: 없음
    """
    probe = _make_probe(image)                          # 축소 탐침 준비

    coarse_candidates = _angle_range(                   # 1단계: 전 범위를 1.0° 간격으로
        -DESKEW_SEARCH_RANGE_DEGREES, DESKEW_SEARCH_RANGE_DEGREES, DESKEW_COARSE_STEP_DEGREES
    )
    coarse_best = _best_angle(probe, coarse_candidates)  # 개략 최적 각도

    fine_low = max(coarse_best - DESKEW_FINE_WINDOW_DEGREES, -DESKEW_SEARCH_RANGE_DEGREES)   # 정밀 하한
    fine_high = min(coarse_best + DESKEW_FINE_WINDOW_DEGREES, DESKEW_SEARCH_RANGE_DEGREES)   # 정밀 상한
    fine_candidates = _angle_range(fine_low, fine_high, DESKEW_FINE_STEP_DEGREES)            # 2단계 후보
    fine_best = _best_angle(probe, fine_candidates)                                          # 정밀 최적 각도

    logger.debug("기울기 추정 개략=%.2f도 정밀=%.2f도", coarse_best, fine_best)  # 근거 기록
    return fine_best                                    # 최종 보정 각도


def _angle_range(low: float, high: float, step: float) -> tuple[float, ...]:
    """[low, high] 를 step 간격으로 나눈 각도 후보를 만든다 (부동소수 반올림 고정).

    입력  : low/high — 범위, step — 간격
    출력  : 각도 튜플 (소수 둘째 자리로 반올림해 같은 입력 → 같은 후보 보장)
    부수효과: 없음
    """
    count = int(round((high - low) / step))             # 구간 개수
    return tuple(round(low + i * step, 2) for i in range(count + 1))  # 양 끝 포함


def _best_angle(probe: np.ndarray, candidates: tuple[float, ...]) -> float:
    """후보 각도 중 점수가 가장 높은 각도를 고른다.

    입력  : probe — 탐침 배열, candidates — 각도 후보
    출력  : 최적 각도. 동점이면 0 에 가까운 쪽(불필요한 회전을 줄인다)
    부수효과: 없음
    """
    best_angle = 0.0                                    # 기본값
    best_score = -1.0                                   # 어떤 점수보다도 낮은 초기값
    for angle in candidates:                            # 후보 순회 (정렬된 순서라 결과가 재현된다)
        score = _score_at_angle(probe, angle, square=False)               # 각도별 점수
        if score > best_score or (score == best_score and abs(angle) < abs(best_angle)):
            best_angle = angle                          # 더 좋거나, 동점이면 회전량이 작은 쪽
            best_score = score                          # 최고 점수 갱신
    return best_angle                                   # 최적 각도


# ══════════════════════════════════════════════════════════════════
#  ③ 해상도 정규화
# ══════════════════════════════════════════════════════════════════

def plan_scale(size: tuple[int, int], target_long_edge: int = TARGET_LONG_EDGE_PX) -> float:
    """장변을 목표에 맞추는 배율을 계산한다.

    역할  : 초과분은 축소해 재현성을 지키고, 지나치게 작은 것만 제한적으로 확대한다.
    입력  : size — (너비, 높이), target_long_edge — 장변 목표 픽셀
    출력  : 배율 (1.0 이면 변경하지 않음)
    부수효과: 없음
    """
    long_edge = max(size)                               # 현재 장변
    if long_edge <= 0:                                  # 외부 경계 방어 (빈 이미지)
        raise ValueError(f"이미지 크기가 올바르지 않음: {size!r}")
    if long_edge > target_long_edge:                    # 상한 초과 → 축소한다
        return target_long_edge / float(long_edge)      # 정확히 목표 장변이 되는 배율
    if long_edge <= target_long_edge * UPSCALE_TRIGGER_RATIO:  # 목표의 절반 이하로 작을 때만
        return min(MAX_UPSCALE_FACTOR, target_long_edge / float(long_edge))  # 최대 2배까지만 확대
    return 1.0                                          # 그 사이 구간은 손대지 않는다


# ══════════════════════════════════════════════════════════════════
#  전처리 본체
# ══════════════════════════════════════════════════════════════════

def preprocess(
    image,
    *,
    target_long_edge: int = TARGET_LONG_EDGE_PX,
    correct_orientation: bool = True,
    correct_skew: bool = True,
) -> Preprocessed:
    """페이지 이미지를 VLM 입력 형태로 정리하고 좌표 변환을 기록한다.

    역할  : ① 90도 방향 보정 → ② 기울기 보정 → ③ 해상도 정규화 를 순서대로 적용하고,
            원본 ↔ 결과의 아핀 변환을 `PageTransform` 으로 남긴다.
    입력  : image — PIL 이미지 / 파일 경로(str·Path),
            target_long_edge — 장변 목표(px),
            correct_orientation / correct_skew — 개별 단계 사용 여부(실험·테스트용)
    출력  : Preprocessed (전처리 이미지 + 변환 기록 + 원본 + 관찰 메모)
    부수효과: 없음. 파일을 쓰지 않고 입력 이미지를 변형하지 않는다(항상 새 객체).
    """
    source = _load_image(image)                         # 경로든 이미지든 PIL 이미지로 통일
    original = _to_rgb(source)                          # 채널 통일 (회전 배경·PNG 인코딩 대비)
    original_size = (original.width, original.height)   # 원본 크기 기록 (역변환의 기준)

    current = original                                  # 단계별로 갱신할 작업 이미지
    matrix: Matrix = IDENTITY                           # 누적 정변환 (원본 → 현재)
    notes: list[str] = []                               # 관찰 메모

    # ── ① 90도 단위 방향 보정 ─────────────────────────────────
    orientation = estimate_orientation(current) if correct_orientation else 0  # 0 또는 90
    if orientation:                                     # 0 이면 아무것도 하지 않는다
        step_matrix, new_w, new_h = rotation_matrix_with_expand(              # 좌표 대응 계산
            current.width, current.height, float(orientation)
        )
        # 90° 배수는 transpose 로 돌려야 보간 없이 무손실이다 (화질 손실 0).
        current = current.transpose(Image.ROTATE_90)                          # PIL 의 ROTATE_90 은 반시계
        assert (current.width, current.height) == (new_w, new_h), "90도 회전 크기 계산 불일치"
        matrix = compose(step_matrix, matrix)                                 # 정변환 누적
        notes.append(f"방향 보정 {orientation}도(반시계)")                      # 관찰 기록
        logger.info("방향 보정 적용: %d도", orientation)                        # 운영 로그

    # ── ② 기울기 보정 ────────────────────────────────────────
    skew = estimate_skew(current) if correct_skew else 0.0                    # 추정 보정 각도
    applied_skew = 0.0                                                        # 실제로 적용한 각도
    if abs(skew) > DESKEW_MIN_APPLY_DEGREES:            # 0.5° 이하는 재샘플링 손실이 이득보다 크다
        step_matrix, new_w, new_h = rotation_matrix_with_expand(              # 좌표 대응 + 확장 크기
            current.width, current.height, skew
        )
        # PIL 의 transform 은 "출력 → 입력" 사상을 요구하므로 역행렬을 넘긴다.
        # 이렇게 하면 픽셀 재배치와 좌표 역변환이 같은 행렬을 쓰게 되어 어긋날 수 없다.
        coeffs = to_pil_affine_coeffs(invert(step_matrix))                    # PIL 계수로 변환
        current = current.transform(                                          # 아핀 변환 실행
            (new_w, new_h),                                                   # 확장된 캔버스 크기
            Image.AFFINE,                                                     # 아핀 모드
            coeffs,                                                           # 출력→입력 계수
            resample=Image.BICUBIC,                                           # 미세 회전은 BICUBIC 이 무난
            fillcolor=BACKGROUND_RGB,                                         # 빈 영역은 흰색(지면과 동일)
        )
        matrix = compose(step_matrix, matrix)                                 # 정변환 누적
        applied_skew = skew                                                   # 적용 각도 기록
        notes.append(f"기울기 보정 {skew:+.1f}도")                              # 관찰 기록
        logger.info("기울기 보정 적용: %+.2f도", skew)                          # 운영 로그
    elif skew:                                          # 추정은 했지만 적용하지 않은 경우도 남긴다
        notes.append(f"기울기 {skew:+.1f}도 추정(임계 {DESKEW_MIN_APPLY_DEGREES}도 이하로 미적용)")

    # ── ③ 해상도 정규화 ───────────────────────────────────────
    scale = plan_scale((current.width, current.height), target_long_edge)     # 적용할 배율
    if scale != 1.0:                                    # 1.0 이면 재샘플링하지 않는다
        before_w, before_h = current.width, current.height                    # 확대축소 전 크기 (실제 배율 계산용)
        new_w = max(int(round(before_w * scale)), 1)                          # 새 너비 (최소 1px)
        new_h = max(int(round(before_h * scale)), 1)                          # 새 높이
        # 축소는 LANCZOS(모아레·계단현상 억제), 확대는 BICUBIC(과한 링잉 방지).
        resample = Image.LANCZOS if scale < 1.0 else Image.BICUBIC
        current = current.resize((new_w, new_h), resample)                    # 실제 확대축소
        # 반올림 때문에 축별 실제 배율이 목표 배율과 미세하게 다르다.
        # 좌표가 어긋나지 않으려면 "실제로 일어난" 배율을 행렬에 써야 한다.
        actual_sx = new_w / float(before_w)                                   # 가로 실제 배율
        actual_sy = new_h / float(before_h)                                   # 세로 실제 배율
        matrix = compose(scale_matrix(actual_sx, actual_sy), matrix)           # 정변환 누적
        notes.append(f"해상도 정규화 x{scale:.3f} → {new_w}x{new_h}")            # 관찰 기록
        logger.info("해상도 정규화: x%.3f → %dx%d", scale, new_w, new_h)         # 운영 로그

    transform = PageTransform.build(                    # 변환 기록 완성 (역행렬은 내부에서 계산)
        original_size=original_size,                    # 원본 크기
        processed_size=(current.width, current.height),  # 결과 크기
        orientation_degrees=orientation,                # 적용한 방향 보정
        deskew_degrees=applied_skew,                    # 적용한 기울기 보정
        scale=scale,                                    # 적용한 배율
        forward=matrix,                                 # 누적 정변환
    )
    return Preprocessed(image=current, transform=transform, original=original, notes=tuple(notes))


def _load_image(image) -> Image.Image:
    """입력을 PIL 이미지로 통일한다.

    입력  : PIL 이미지 / 파일 경로(str·Path)
    출력  : PIL 이미지
    부수효과: 경로가 오면 파일을 읽는다. 다른 타입은 즉시 TypeError.
    """
    if isinstance(image, Image.Image):                  # 이미 이미지면 그대로 쓴다
        return image
    if isinstance(image, (str, Path)):                  # 경로면 열어서 메모리로 올린다
        with Image.open(image) as opened:               # with 로 파일 핸들을 즉시 닫는다
            return opened.copy()                        # 지연 로딩 이미지가 아니라 사본을 돌려준다
    raise TypeError(f"이미지 또는 경로가 필요하다: {type(image)!r}")  # 그 외는 호출자 실수
