# -*- coding: utf-8 -*-
"""③-b VLM PARSER fixture 생성 스크립트 — 합성 이미지만 만든다.

왜 합성인가
    raw_file/ 의 회사 문서를 fixtures/ 로 복사하면 git 추적 대상이라 커밋되어 버린다.
    반출 이슈가 있으므로 fixture 는 전부 코드로 그린다. 이 스크립트가 그 재현 수단이다.

실행
    <repo>/.venv/Scripts/python.exe fixtures/vlm/make_fixtures.py

재현성
    난수와 현재시각을 쓰지 않는다. 손글씨 느낌 체크도 고정된 좌표 목록으로 그린다.
    같은 pillow 버전에서 같은 바이트가 나온다.
"""
from __future__ import annotations                      # 타입 표기 일관성 유지

from pathlib import Path                                # 출력 경로 계산

from PIL import Image, ImageDraw, ImageFont             # 이미지 생성·그리기·폰트

# 이 스크립트가 있는 폴더가 fixture 루트다.
FIXTURES_DIR: Path = Path(__file__).resolve().parent

# 생성한 PNG 를 넣을 폴더.
IMAGES_DIR: Path = FIXTURES_DIR / "images"

# mock VLM 응답(기대 출력)을 넣을 폴더.
MOCK_DIR: Path = FIXTURES_DIR / "mock_responses"

# 기본 페이지 크기 — A4 150dpi 근사.
#   장변 1754px 는 목표(2576)의 절반(1288)보다 크고 목표보다 작아서
#   해상도 정규화가 '아무것도 하지 않는' 구간이다. 기준 케이스로 적당하다.
PAGE_WIDTH: int = 1240
PAGE_HEIGHT: int = 1754

# 지면 색과 잉크 색.
PAPER = (255, 255, 255)                                 # 흰 종이
INK = (20, 20, 20)                                      # 검은 잉크 (완전 0 보다 스캔물에 가깝다)


def _font(size: int) -> ImageFont.FreeTypeFont:
    """pillow 에 내장된 기본 폰트를 주어진 크기로 얻는다.

    역할  : 시스템 폰트에 의존하면 기계마다 결과가 달라진다. pillow 내장 폰트만 쓴다.
    입력  : size — 글자 크기(px)
    출력  : 폰트 객체
    부수효과: 없음
    """
    try:
        return ImageFont.load_default(size=size)        # pillow 10.1+ : 내장 TTF 를 크기 지정해 로드
    except TypeError:                                   # 아주 오래된 pillow 대비 (크기 인자 미지원)
        return ImageFont.load_default()                 # 비트맵 기본 폰트


# ══════════════════════════════════════════════════════════════════
#  기본 데이터시트 그리기
# ══════════════════════════════════════════════════════════════════

# 데이터시트 본문 행 — (라벨, 값). MVP 9필드가 모두 들어가도록 구성했다.
DATASHEET_ROWS: tuple[tuple[str, str], ...] = (
    ("TAG NO.", "10FV001"),
    ("APPLICATION", "RECYCLE TO DHC FEED FILTERS"),
    ("LINE NO. / SIZE", "10-P-1042-A1A / 4 IN"),
    ("BODY SIZE", '4"'),
    ("BODY RATING", "300#"),
    ("BODY MATERIAL", "A216 WCB"),
    ("MODEL NO.", "ED-667"),
    ("TRIM MATERIAL", "316 SST"),
    ("VALVE COEFFICIENT", "236"),
    ("REQ'D FLOW COEFF., CV", "126"),
    ("INLET PRESSURE MAX", "12.5 KG/CM2G"),
    ("INLET TEMPERATURE MAX", "185 DEG C"),
    ("ACTUATOR TYPE", "PNEUMATIC DIAPHRAGM"),
    ("ACT'N FAIL POSITION", "CLOSE"),
    ("POSITIONER MODEL NO.", "3582i"),
    ("AIR SUPPLY", "5.6 KG/CM2G"),
)


def _draw_datasheet(
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    *,
    scale: float = 1.0,
) -> None:
    """데이터시트 모양의 표와 머리글·꼬리말을 그린다.

    역할  : 기울기·방향·해상도 테스트에 쓸 '글줄이 뚜렷한' 페이지를 만든다.
            제조사는 라벨 없이 좌측 상단 로고와 꼬리말에만 둔다(실제 양식과 같은 조건).
    입력  : draw — ImageDraw, width/height — 캔버스 크기, scale — 글자·간격 배율
    출력  : 없음
    부수효과: draw 대상 이미지에 그린다.
    """
    title_font = _font(int(30 * scale))                 # 제목용 큰 글자
    label_font = _font(int(19 * scale))                 # 라벨용
    value_font = _font(int(19 * scale))                 # 값용
    small_font = _font(int(15 * scale))                 # 꼬리말용

    margin = int(70 * scale)                            # 페이지 여백
    # ── 머리글: 라벨 없는 제조사(좌측 상단 로고 위치) ──────────
    draw.rectangle(                                     # 로고 테두리 상자
        (margin, int(40 * scale), margin + int(230 * scale), int(40 * scale) + int(50 * scale)),
        outline=INK, width=max(int(2 * scale), 1),
    )
    draw.text((margin + int(14 * scale), int(50 * scale)), "FISHER", font=title_font, fill=INK)
    draw.text(                                          # 제목 (오른쪽)
        (margin + int(300 * scale), int(52 * scale)),
        "CONTROL VALVE SPECIFICATION SHEET", font=label_font, fill=INK,
    )

    # ── 본문 표 ────────────────────────────────────────────
    top = int(130 * scale)                              # 표 시작 y
    row_height = int(38 * scale)                         # 행 높이 (행간이 투영 프로파일의 신호원이다)
    label_x = margin + int(10 * scale)                  # 라벨 x
    value_x = margin + int(430 * scale)                 # 값 x
    table_right = width - margin                        # 표 오른쪽 경계

    for index, (label, value) in enumerate(DATASHEET_ROWS):          # 행마다 그린다
        row_top = top + index * row_height                            # 이 행의 y
        draw.line((margin, row_top, table_right, row_top), fill=INK, width=1)  # 행 구분선
        draw.text((label_x, row_top + int(8 * scale)), label, font=label_font, fill=INK)   # 라벨
        draw.text((value_x, row_top + int(8 * scale)), value, font=value_font, fill=INK)   # 값

    table_bottom = top + len(DATASHEET_ROWS) * row_height             # 표 아래쪽
    draw.line((margin, table_bottom, table_right, table_bottom), fill=INK, width=1)  # 마감선
    draw.line((margin, top, margin, table_bottom), fill=INK, width=1)               # 좌측 테두리
    draw.line((table_right, top, table_right, table_bottom), fill=INK, width=1)      # 우측 테두리
    draw.line((value_x - int(20 * scale), top, value_x - int(20 * scale), table_bottom),
              fill=INK, width=1)                                                     # 라벨/값 구분선

    # ── 체크박스 행 (직접 표기) ────────────────────────────
    checkbox_top = table_bottom + int(40 * scale)                     # 체크박스 구역 시작
    _draw_checkbox_row(
        draw, margin, checkbox_top, scale,
        label="Air Fails Valve to",
        options=(("Lock", False), ("Open", False), ("Close", True)),
    )

    # ── 꼬리말: 라벨 없는 제조사 재등장 ─────────────────────
    draw.text(
        (margin, height - int(60 * scale)),
        "FISHER CONTROLS INTERNATIONAL LLC     FORM 1234 (REV. 3)",
        font=small_font, fill=INK,
    )


def _draw_checkbox_row(
    draw: ImageDraw.ImageDraw,
    left: int,
    top: int,
    scale: float,
    *,
    label: str,
    options: tuple[tuple[str, bool], ...],
    handwritten: bool = False,
    box_before_text: bool = False,
) -> int:
    """체크박스 한 행을 그린다.

    역할  : Fisher 계열 양식처럼 값이 텍스트가 아니라 체크박스인 행을 재현한다.
    입력  : draw — ImageDraw, left/top — 시작 좌표, scale — 배율,
            label — 항목명, options — (칸 문구, 체크 여부) 목록,
            handwritten — 손글씨 느낌으로 체크할지, box_before_text — 상자를 문구 앞에 둘지
    출력  : 이 행의 아래쪽 y 좌표
    부수효과: draw 대상 이미지에 그린다.
    """
    label_font = _font(int(19 * scale))                  # 라벨 글꼴
    option_font = _font(int(18 * scale))                 # 칸 문구 글꼴
    box_size = int(20 * scale)                           # 체크박스 한 변
    draw.text((left, top), label, font=label_font, fill=INK)          # 항목명
    cursor = left + int(330 * scale)                     # 첫 칸의 x

    for text, checked in options:                        # 칸마다 그린다
        if box_before_text:                              # "[X] Opens" 형태
            box_x = cursor                               # 상자를 먼저
            text_x = cursor + box_size + int(8 * scale)  # 문구는 그 뒤
            cursor = text_x + int(len(text) * 11 * scale) + int(40 * scale)  # 다음 칸으로
        else:                                            # "Open [ ]" 형태
            text_x = cursor                              # 문구를 먼저
            box_x = cursor + int(len(text) * 11 * scale) + int(10 * scale)   # 상자는 그 뒤
            cursor = box_x + box_size + int(45 * scale)  # 다음 칸으로
        draw.text((text_x, top + int(2 * scale)), text, font=option_font, fill=INK)  # 칸 문구
        box = (box_x, top, box_x + box_size, top + box_size)                        # 상자 사각형
        draw.rectangle(box, outline=INK, width=max(int(2 * scale), 1))               # 상자 테두리
        if checked and handwritten:                      # 손으로 그린 듯한 체크
            _draw_handwritten_check(draw, box, scale)
        elif checked:                                    # 인쇄 X 체크
            draw.line((box[0] + 3, box[1] + 3, box[2] - 3, box[3] - 3), fill=INK, width=2)
            draw.line((box[0] + 3, box[3] - 3, box[2] - 3, box[1] + 3), fill=INK, width=2)
    return top + box_size + int(10 * scale)              # 다음 행이 시작할 y


def _draw_handwritten_check(
    draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], scale: float
) -> None:
    """손글씨 느낌의 체크 표시를 그린다 (난수 없이 고정 좌표로).

    역할  : 1980년대 스캔본에는 손으로 그린 체크가 흔하다. 그 형태를 재현한다.
    입력  : draw — ImageDraw, box — 상자 사각형, scale — 배율
    출력  : 없음
    부수효과: draw 대상 이미지에 그린다.
    """
    x0, y0, x1, y1 = box                                 # 상자 좌표
    width = x1 - x0                                      # 상자 너비
    height = y1 - y0                                     # 상자 높이
    # 상자를 살짝 넘치는 비뚤한 V 자. 고정 비율 좌표라 실행마다 같다.
    points = [
        (x0 - width * 0.10, y0 + height * 0.35),         # 시작 (상자 왼쪽 밖)
        (x0 + width * 0.22, y0 + height * 0.62),         # 꺾이기 전
        (x0 + width * 0.38, y1 - height * 0.05),         # 꺾이는 점 (아래로 삐침)
        (x0 + width * 0.72, y0 + height * 0.18),         # 올라가는 획
        (x1 + width * 0.18, y0 - height * 0.22),         # 끝 (상자 위쪽 밖)
    ]
    draw.line(points, fill=INK, width=max(int(3 * scale), 2), joint="curve")  # 이어진 획으로


def _render_page(width: int, height: int, scale: float = 1.0) -> Image.Image:
    """데이터시트 한 장을 그려 반환한다."""
    image = Image.new("RGB", (width, height), PAPER)      # 흰 종이
    draw = ImageDraw.Draw(image)                          # 그리기 도구
    _draw_datasheet(draw, width, height, scale=scale)     # 내용 그리기
    return image                                          # 완성된 페이지


# ══════════════════════════════════════════════════════════════════
#  체크박스 전용 케이스
# ══════════════════════════════════════════════════════════════════

def _render_checkbox_case(
    name: str,
    rows: tuple[dict, ...],
    *,
    width: int = 1240,
    height: int = 700,
) -> Image.Image:
    """체크박스 행들만 있는 작은 페이지를 그린다.

    입력  : name — 페이지 제목, rows — _draw_checkbox_row 인자 dict 목록,
            width/height — 캔버스 크기
    출력  : PIL 이미지
    부수효과: 없음
    """
    image = Image.new("RGB", (width, height), PAPER)      # 흰 종이
    draw = ImageDraw.Draw(image)                          # 그리기 도구
    draw.text((70, 40), name, font=_font(24), fill=INK)   # 제목 (사람이 파일을 구분하기 쉽게)
    cursor_y = 120                                        # 첫 행 y
    for row in rows:                                      # 행마다 그린다
        cursor_y = _draw_checkbox_row(draw, 70, cursor_y, 1.0, **row) + 34  # 행 간격 34px
    return image                                          # 완성된 페이지


# ══════════════════════════════════════════════════════════════════
#  mock VLM 응답 (기대 출력)
# ══════════════════════════════════════════════════════════════════

# 정상 데이터시트에 대한 기대 응답. 원문 보존 규칙을 지킨 '좋은' 응답이다.
#   bbox 는 전처리 이미지 기준 0~1 정규화 좌표다.
MOCK_CLEAN_EXTRACTION: str = """{
  "extractions": [
    {"field_key": "engineering_tag_no", "raw_value": "10FV001", "raw_label": "TAG NO.",
     "row_text": null, "bbox": [0.35, 0.075, 0.52, 0.093], "confidence": 0.97,
     "absence_reason": "present", "note": ""},
    {"field_key": "manufacturer", "raw_value": "FISHER", "raw_label": "(좌측 상단 로고)",
     "row_text": null, "bbox": [0.06, 0.026, 0.24, 0.052], "confidence": 0.93,
     "absence_reason": "present", "note": "항목 라벨 없이 로고와 꼬리말에만 있음"},
    {"field_key": "model_no", "raw_value": "ED-667", "raw_label": "MODEL NO.",
     "row_text": null, "bbox": [0.35, 0.208, 0.47, 0.226], "confidence": 0.95,
     "absence_reason": "present", "note": ""},
    {"field_key": "valve_body_rating", "raw_value": "300#", "raw_label": "BODY RATING",
     "row_text": null, "bbox": [0.35, 0.164, 0.44, 0.182], "confidence": 0.94,
     "absence_reason": "present", "note": ""},
    {"field_key": "valve_body_size", "raw_value": "4\\"", "raw_label": "BODY SIZE",
     "row_text": null, "bbox": [0.35, 0.142, 0.42, 0.160], "confidence": 0.92,
     "absence_reason": "present", "note": ""},
    {"field_key": "valve_body_material", "raw_value": "A216 WCB", "raw_label": "BODY MATERIAL",
     "row_text": null, "bbox": [0.35, 0.186, 0.50, 0.204], "confidence": 0.94,
     "absence_reason": "present", "note": ""},
    {"field_key": "actuator_fail_action", "raw_value": "CLOSE", "raw_label": "ACT'N FAIL POSITION",
     "row_text": "ACT'N FAIL POSITION    CLOSE", "bbox": [0.35, 0.428, 0.46, 0.446],
     "confidence": 0.91, "absence_reason": "present",
     "note": "직접 표기(고장 시 위치)로 보이는 라벨. 판단은 하류에 맡김"},
    {"field_key": "rated_cv_normal", "raw_value": "236", "raw_label": "VALVE COEFFICIENT",
     "row_text": null, "bbox": [0.35, 0.296, 0.43, 0.314], "confidence": 0.96,
     "absence_reason": "present", "note": ""},
    {"field_key": "required_cv", "raw_value": "126", "raw_label": "REQ'D FLOW COEFF., CV",
     "row_text": null, "bbox": [0.35, 0.318, 0.43, 0.336], "confidence": 0.96,
     "absence_reason": "present", "note": ""}
  ]
}"""

# 없음 vs 판독불가를 구분해 보고한 응답. note 접두사 규약 검증에 쓴다.
MOCK_ABSENCE_EXTRACTION: str = """{
  "extractions": [
    {"field_key": "engineering_tag_no", "raw_value": "10FV001", "raw_label": "TAG NO.",
     "row_text": null, "bbox": [0.35, 0.075, 0.52, 0.093], "confidence": 0.97,
     "absence_reason": "present", "note": ""},
    {"field_key": "manufacturer", "raw_value": null, "raw_label": null,
     "row_text": null, "bbox": null, "confidence": 0.0,
     "absence_reason": "no_evidence", "note": "로고·머리글·꼬리말에 회사명이 없음"},
    {"field_key": "model_no", "raw_value": null, "raw_label": "MODEL NO.",
     "row_text": null, "bbox": [0.35, 0.208, 0.47, 0.226], "confidence": 0.2,
     "absence_reason": "unreadable", "note": "값 칸이 번져 글자를 특정할 수 없음"},
    {"field_key": "valve_body_rating", "raw_value": "300#", "raw_label": "BODY RATING",
     "row_text": null, "bbox": [0.35, 0.164, 0.44, 0.182], "confidence": 0.94,
     "absence_reason": "present", "note": ""},
    {"field_key": "valve_body_size", "raw_value": "4\\"", "raw_label": "BODY SIZE",
     "row_text": null, "bbox": [0.35, 0.142, 0.42, 0.160], "confidence": 0.92,
     "absence_reason": "present", "note": ""},
    {"field_key": "valve_body_material", "raw_value": "A216 WCB", "raw_label": "BODY MATERIAL",
     "row_text": null, "bbox": [0.35, 0.186, 0.50, 0.204], "confidence": 0.94,
     "absence_reason": "present", "note": ""},
    {"field_key": "actuator_fail_action", "raw_value": null, "raw_label": "Air Fails Valve to",
     "row_text": "Air Fails Valve to   Lock [ ]   Open [ ]   Close [ ]", "bbox": [0.06, 0.70, 0.80, 0.73],
     "confidence": 0.1, "absence_reason": "checkbox_ambiguous",
     "note": "세 칸 모두 표시가 없음"},
    {"field_key": "rated_cv_normal", "raw_value": "236", "raw_label": "VALVE COEFFICIENT",
     "row_text": null, "bbox": [0.35, 0.296, 0.43, 0.314], "confidence": 0.96,
     "absence_reason": "present", "note": ""},
    {"field_key": "required_cv", "raw_value": null, "raw_label": null,
     "row_text": null, "bbox": null, "confidence": 0.0,
     "absence_reason": "no_evidence", "note": "요구 Cv 항목이 이 양식에 없음"}
  ]
}"""

# 직접/역전 두 라벨이 한 페이지에 있는 케이스의 응답.
#   파서는 어느 쪽인지 판단하지 않고 라벨과 값을 그대로 옮긴다.
MOCK_FAIL_ACTION_PAIR: str = """{
  "extractions": [
    {"field_key": "actuator_fail_action", "raw_value": "Close", "raw_label": "Air Fails Valve to",
     "row_text": "Air Fails Valve to   Lock [ ]   Open [ ]   Close [X]",
     "bbox": [0.05, 0.16, 0.78, 0.21], "confidence": 0.88, "absence_reason": "present",
     "note": "체크박스 3칸 중 Close 에만 표시. 같은 페이지에 'Increase Signal Valve' 행도 있어 함께 관찰함"}
  ]
}"""

# 크롭 재판독 응답 (항목 1개).
MOCK_REREAD_ITEM: str = """{
  "field_key": "actuator_fail_action", "raw_value": "Close", "raw_label": "Air Fails Valve to",
  "row_text": "Air Fails Valve to   Lock [ ]   Open [ ]   Close [X]",
  "bbox": [0.10, 0.30, 0.95, 0.70], "confidence": 0.9, "absence_reason": "present",
  "note": "확대 후에도 Close 칸에만 표시가 보임"
}"""


# ══════════════════════════════════════════════════════════════════
#  생성 진입점
# ══════════════════════════════════════════════════════════════════

def build_all() -> dict[str, Path]:
    """모든 fixture 를 만들어 파일로 쓴다.

    역할  : 전처리(정상/기울기/회전/고해상도/저해상도)와 체크박스(인쇄/손글씨/무체크/복수),
            직접·역전 라벨 동시 등장 케이스, mock 응답까지 한 번에 생성한다.
    입력  : 없음
    출력  : {이름: 경로} 사전
    부수효과: fixtures/vlm/images 와 fixtures/vlm/mock_responses 에 파일을 쓴다.
    """
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)        # 이미지 폴더 준비
    MOCK_DIR.mkdir(parents=True, exist_ok=True)          # mock 응답 폴더 준비
    written: dict[str, Path] = {}                        # 생성 결과 기록

    # ── ① 정상 ────────────────────────────────────────────
    clean = _render_page(PAGE_WIDTH, PAGE_HEIGHT)                     # 기준 페이지
    written["clean"] = _save(clean, "clean.png")                      # 장변 1754 → 배율 1.0 구간

    # ── ② 3도 기울어짐 ────────────────────────────────────
    #   시계 방향 3도로 그린다 → 전처리는 반시계 +3도를 찾아 보정해야 한다.
    skewed = clean.rotate(-3.0, resample=Image.BICUBIC, expand=True, fillcolor=PAPER)
    written["skew3"] = _save(skewed, "skew3.png")

    # ── ③ 임계 이하 미세 기울기 (0.3도) ─────────────────────
    #   |angle| <= 0.5도 이므로 회전을 적용하지 않아야 한다.
    micro = clean.rotate(-0.3, resample=Image.BICUBIC, expand=True, fillcolor=PAPER)
    written["micro_skew"] = _save(micro, "micro_skew.png")

    # ── ④ 90도 회전 (누운 페이지) ──────────────────────────
    #   시계 방향 90도로 눕힌다 → 전처리가 반시계 90도를 적용하면 원래대로 선다.
    rotated = clean.transpose(Image.ROTATE_270)                       # ROTATE_270 = 시계 90도
    written["rot90"] = _save(rotated, "rot90.png")

    # ── ⑤ 고해상도 (장변 2576 초과) ────────────────────────
    #   A4 300dpi 근사. 장변 3508 → 축소되어야 한다.
    highres = _render_page(2480, 3508, scale=2.0)
    written["highres"] = _save(highres, "highres.png")

    # ── ⑥ 저해상도 (목표의 절반 이하) ───────────────────────
    #   장변 877 ≤ 1288 → 최대 2배까지 확대되어야 한다.
    lowres = clean.resize((PAGE_WIDTH // 2, PAGE_HEIGHT // 2), Image.LANCZOS)
    written["lowres"] = _save(lowres, "lowres.png")

    # ── ⑦ 체크박스: 인쇄 체크 ───────────────────────────────
    printed = _render_checkbox_case("CHECKBOX - PRINTED MARK", (
        {"label": "Air Fails Valve to",
         "options": (("Lock", False), ("Open", False), ("Close", True))},
        {"label": "Increase Signal Valve", "box_before_text": True,
         "options": (("Opens", True), ("Closes", False))},
    ))
    written["checkbox_printed"] = _save(printed, "checkbox_printed.png")

    # ── ⑧ 체크박스: 손글씨 느낌 체크 ─────────────────────────
    hand = _render_checkbox_case("CHECKBOX - HANDWRITTEN MARK", (
        {"label": "Air Fails Valve to", "handwritten": True,
         "options": (("Lock", False), ("Open", True), ("Close", False))},
        {"label": "Increase Signal Valve", "handwritten": True, "box_before_text": True,
         "options": (("Opens", False), ("Closes", True))},
    ))
    written["checkbox_hand"] = _save(hand, "checkbox_hand.png")

    # ── ⑨ 체크박스: 무체크 ─────────────────────────────────
    none_marked = _render_checkbox_case("CHECKBOX - NO MARK", (
        {"label": "Air Fails Valve to",
         "options": (("Lock", False), ("Open", False), ("Close", False))},
    ))
    written["checkbox_none"] = _save(none_marked, "checkbox_none.png")

    # ── ⑩ 체크박스: 복수 체크 ───────────────────────────────
    multi = _render_checkbox_case("CHECKBOX - MULTIPLE MARKS", (
        {"label": "Air Fails Valve to",
         "options": (("Lock", False), ("Open", True), ("Close", True))},
    ))
    written["checkbox_multi"] = _save(multi, "checkbox_multi.png")

    # ── ⑪ 직접·역전 라벨 동시 등장 + 헷갈리는 인접 행 ──────────
    #   "Air Fails Valve to"(직접), "Increase Signal Valve"(역전),
    #   그리고 둘과 헷갈리는 "Air to Actuator" 를 한 페이지에 함께 그린다.
    pair = _render_checkbox_case("FAIL ACTION - DIRECT vs INVERTED", (
        {"label": "Air Fails Valve to",
         "options": (("Lock", False), ("Open", False), ("Close", True))},
        {"label": "Air to Actuator", "box_before_text": True,
         "options": (("Top", True), ("Bottom", False))},
        {"label": "Increase Signal Valve", "box_before_text": True,
         "options": (("Opens", True), ("Closes", False))},
        {"label": "Air-to-Open (ATO)", "box_before_text": True,
         "options": (("Yes", True), ("No", False))},
    ), height=780)
    written["fail_action_pair"] = _save(pair, "fail_action_pair.png")

    # ── mock VLM 응답 ─────────────────────────────────────
    written["mock_clean"] = _save_text(MOCK_CLEAN_EXTRACTION, "clean_extraction.json")
    written["mock_absence"] = _save_text(MOCK_ABSENCE_EXTRACTION, "absence_extraction.json")
    written["mock_fail_pair"] = _save_text(MOCK_FAIL_ACTION_PAIR, "fail_action_pair.json")
    written["mock_reread"] = _save_text(MOCK_REREAD_ITEM, "reread_item.json")

    return written                                       # 생성 목록


def _save(image: Image.Image, filename: str) -> Path:
    """이미지를 PNG 로 저장한다 (같은 입력 → 같은 바이트)."""
    path = IMAGES_DIR / filename                         # 저장 경로
    image.save(path, format="PNG", optimize=False)        # optimize=False 로 결정론 유지
    return path                                          # 경로 반환


def _save_text(text: str, filename: str) -> Path:
    """mock 응답 문자열을 파일로 저장한다."""
    path = MOCK_DIR / filename                           # 저장 경로
    path.write_text(text, encoding="utf-8")              # UTF-8 로 기록
    return path                                          # 경로 반환


if __name__ == "__main__":                               # 스크립트로 직접 실행할 때만
    for key, target in build_all().items():              # 생성 결과를 훑는다
        print(f"{key:20s} -> {target.relative_to(FIXTURES_DIR.parent.parent)}")  # 상대 경로로 보고
