# -*- coding: utf-8 -*-
"""VLM 모듈 테스트용 fixture.

두 가지만 제공한다.
    ① `load_fixture_image` — 합성 fixture 이미지 적재 (기하 전처리 테스트용)
    ② `fake_openai` — OpenAI Chat Completions 를 흉내낸 mock 클라이언트
       (`VlmParser(client=...)` 로 주입한다. 실제 네트워크 호출은 없다)

fixture 는 전부 코드로 생성한 합성 데이터다. `raw_file` 의 회사 문서를 복사하지 않는다 —
`fixtures/` 는 git 추적 대상이라 커밋되어 버린다.
"""
from __future__ import annotations                      # 타입 표기 일관성 유지

import json                                             # mock 응답 조립
import sys                                              # 저장소 루트를 import 경로에 추가
from dataclasses import dataclass, field                # mock 자료구조
from pathlib import Path                                # 경로 처리

import pytest                                           # fixture 정의
from PIL import Image                                   # 이미지 적재

# 저장소 루트를 import 경로에 넣는다 (pytest.ini 가 없는 경로에서 직접 실행할 때 대비).
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

IMAGES_DIR = REPO_ROOT / "fixtures" / "vlm" / "images"          # 합성 이미지 위치


@pytest.fixture
def load_fixture_image():
    """이름으로 합성 fixture 이미지를 읽어 준다.

    입력  : (반환된 함수에) 이미지 이름 — 확장자 없이
    출력  : PIL 이미지. 모드는 **파일 그대로** 유지한다
    부수효과: 파일을 읽는다 (쓰기 없음)

    모드를 통일하지 않는 이유:
        한때 이 로더가 `convert("RGB")` 로 채널을 통일했고, 그 때문에
        전처리가 흑백 입력을 RGB 로 부풀리는 동작이 테스트에 가려졌다.
        실물 코퍼스는 100% 가 1비트 이진 스캔이므로 입력 모드를 보존해야
        같은 누락이 재발하지 않는다.
    """
    def _load(name: str) -> Image.Image:
        path = IMAGES_DIR / f"{name}.png"               # 파일 경로
        with Image.open(path) as opened:                # 파일 핸들을 즉시 닫는다
            return opened.copy()                        # 모드 그대로 사본 반환
    return _load


# ══════════════════════════════════════════════════════════════════
#  mock OpenAI 클라이언트 — 실제 API 를 호출하지 않는다
# ══════════════════════════════════════════════════════════════════

@dataclass
class FakeUsage:
    """`response.usage` 를 흉내낸 객체 (비용 추적 코드가 읽는다)."""
    prompt_tokens: int = 100                            # 입력 토큰
    completion_tokens: int = 50                         # 출력 토큰


@dataclass
class FakeMessage:
    """`choices[0].message` 를 흉내낸 객체."""
    content: str                                        # 응답 본문 (JSON 문자열)


@dataclass
class FakeChoice:
    """`choices[0]` 를 흉내낸 객체."""
    message: FakeMessage                                # 메시지
    finish_reason: str = "stop"                         # 정지 사유


@dataclass
class FakeResponse:
    """Chat Completions 응답을 흉내낸 객체."""
    choices: list                                       # 선택지 목록
    usage: FakeUsage = field(default_factory=FakeUsage)  # 사용량


@dataclass
class FakeCompletions:
    """`client.chat.completions` 네임스페이스를 흉내낸 객체."""
    payloads: list                                       # 순서대로 돌려줄 응답 문자열들
    requests: list = field(default_factory=list)         # 받은 요청 기록 (프롬프트 검증용)

    def create(self, **kwargs):
        """호출을 기록하고 미리 정해둔 응답을 돌려준다.

        입력  : kwargs — VlmParser 가 만든 요청
        출력  : FakeResponse
        부수효과: requests 를 갱신한다
        """
        self.requests.append(kwargs)                     # 요청 원문 보관
        index = min(len(self.requests) - 1, len(self.payloads) - 1)   # 부족하면 마지막 반복
        return FakeResponse(choices=[FakeChoice(message=FakeMessage(self.payloads[index]))])


@dataclass
class FakeChat:
    """`client.chat` 네임스페이스."""
    completions: FakeCompletions                         # 하위 네임스페이스


class FakeOpenAI:
    """OpenAI 클라이언트를 흉내낸 주입용 객체.

    역할  : `.chat.completions.create(**kwargs)` 만 제공한다. 네트워크 호출은 없다.
    입력  : payloads — 순서대로 돌려줄 응답 JSON 문자열들
    출력  : FakeOpenAI 인스턴스
    부수효과: 없음
    """

    def __init__(self, *payloads: str) -> None:
        """응답 목록을 받아 chat.completions 네임스페이스를 만든다."""
        if not payloads:                                # 응답이 없으면 테스트 작성 실수다
            raise ValueError("mock 응답을 최소 한 개 넘겨야 한다")
        self.chat = FakeChat(completions=FakeCompletions(payloads=list(payloads)))

    @property
    def requests(self) -> list:
        """받은 요청 목록 (프롬프트·이미지 검증용)."""
        return self.chat.completions.requests           # 위임

    @property
    def call_count(self) -> int:
        """호출 횟수 (캐시가 실제로 호출을 막았는지 확인용)."""
        return len(self.chat.completions.requests)      # 요청 수 = 호출 수


@pytest.fixture
def fake_openai():
    """mock OpenAI 클라이언트를 만드는 팩토리를 준다."""
    def _make(*payloads: str) -> FakeOpenAI:
        return FakeOpenAI(*payloads)
    return _make


def vlm_payload(**fields_by_key) -> str:
    """`openai_vlm` 이 기대하는 응답 JSON 을 만든다 (테스트 보조).

    입력  : field_key=dict(...) 형태의 키워드 인자
    출력  : `{"fields": {...}}` JSON 문자열
    부수효과: 없음
    """
    return json.dumps({"fields": dict(fields_by_key)}, ensure_ascii=False)


def vlm_item(raw_value=None, raw_label=None, bbox=None, confidence=0.9, **extra) -> dict:
    """응답 항목 하나를 만든다 (기본값 + 덮어쓰기)."""
    item = {"raw_value": raw_value, "raw_label": raw_label,
            "bbox": bbox, "confidence": confidence}
    item.update(extra)                                  # row_text 등 추가 키
    return item
