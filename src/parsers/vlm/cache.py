# -*- coding: utf-8 -*-
"""응답 캐시 — (파일 해시, 페이지, 프롬프트 버전) 키로 VLM 응답을 재사용한다.

왜 필요한가
    개발 철학 6 — 같은 입력 → 같은 출력. 그런데 VLM API 는 완전한 결정론이 보장되지
    않는다(같은 요청에도 응답이 미세하게 달라질 수 있다). 파이프라인을 다시 돌렸을 때
    같은 결과가 나오게 하는 현실적인 방법은 응답을 캐시하는 것뿐이다.
    이 계층이 없으면 "개선했는지" 를 측정할 수 없다.
"""
from __future__ import annotations                      # 타입 표기 일관성 유지

import hashlib                                          # 파일·이미지 지문
import logging                                          # 캐시 적중/실패 기록
from pathlib import Path                                # 캐시 디렉터리 조작

from PIL import Image                                   # 이미지 지문 계산 시 타입 확인

from .constants import DEFAULT_CACHE_DIR                # 기본 캐시 위치 (runs/ 아래, git 무시 대상)

logger = logging.getLogger(__name__)                    # 모듈 전용 로거


def hash_source(source_path=None, image: Image.Image | None = None) -> str:
    """캐시 키의 '파일 해시' 성분을 만든다.

    역할  : 같은 원본을 같은 키로 인식하기 위한 지문. 파일이 있으면 파일 바이트를,
            없으면 이미지 픽셀을 해싱한다.
    입력  : source_path — 원본 파일 경로(있으면 우선), image — PIL 이미지
    출력  : sha256 16진 문자열
    부수효과: source_path 가 주어지면 파일을 읽는다.
    """
    digest = hashlib.sha256()                           # 해시 누산기
    if source_path is not None:                         # 원본 파일이 있으면 그것이 가장 정확한 지문이다
        path = Path(source_path)                        # 경로 정규화
        digest.update(path.read_bytes())                # 파일 전체 바이트를 넣는다
        return digest.hexdigest()                       # 16진 문자열로
    if image is None:                                   # 둘 다 없으면 키를 만들 수 없다
        raise ValueError("source_path 또는 image 중 하나는 있어야 한다")
    # 픽셀 지문: 모드·크기·원시 바이트를 모두 넣어야 같은 크기의 다른 이미지가 충돌하지 않는다.
    digest.update(image.mode.encode("utf-8"))           # 채널 구성
    digest.update(f"{image.width}x{image.height}".encode("utf-8"))  # 크기
    digest.update(image.tobytes())                      # 원시 픽셀
    return digest.hexdigest()                           # 16진 문자열


def cache_key(content_hash: str, page: int, prompt_version: str) -> str:
    """캐시 키를 만든다.

    역할  : (파일 해시, 페이지, 프롬프트 버전) 세 성분을 하나의 파일명 안전 문자열로 압축한다.
    입력  : content_hash — 원본 지문, page — 페이지 번호, prompt_version — 프롬프트 버전
    출력  : 캐시 키 문자열
    부수효과: 없음
    """
    raw = f"{content_hash}|p{page}|{prompt_version}"    # 사람이 읽을 수 있는 결합 형태
    # 프롬프트 버전에 파일명에 못 쓰는 문자가 섞일 수 있어 해시로 고정 길이 이름을 만든다.
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ResponseCache:
    """디스크 기반 응답 캐시. 값은 모델이 돌려준 JSON 문자열 원문 그대로 저장한다.

    역할  : 같은 (파일, 페이지, 프롬프트) 조합의 응답을 재사용해 재현성과 비용을 함께 잡는다.
    입력  : cache_dir — 저장 디렉터리 (None 이면 runs/vlm_cache)
    출력  : get/put 메서드
    부수효과: put 시점에 디렉터리를 만들고 파일을 쓴다. get 은 읽기만 한다.
    """

    def __init__(self, cache_dir=None, *, enabled: bool = True) -> None:
        """캐시를 준비한다 (디렉터리는 실제로 쓸 때 만든다)."""
        self.cache_dir = Path(cache_dir) if cache_dir is not None else DEFAULT_CACHE_DIR  # 저장 위치
        self.enabled = enabled                          # False 면 항상 미적중으로 동작 (테스트·디버깅용)

    def _path_for(self, key: str) -> Path:
        """키에 해당하는 파일 경로."""
        return self.cache_dir / f"{key}.json"           # 확장자를 붙여 내용 형식을 드러낸다

    def get(self, key: str) -> str | None:
        """캐시에서 응답 문자열을 읽는다.

        입력  : key — cache_key() 결과
        출력  : 저장된 문자열 또는 None(미적중)
        부수효과: 파일 읽기. 읽기 실패는 미적중으로 취급하고 경고를 남긴다.
        """
        if not self.enabled:                            # 비활성이면 항상 미적중
            return None
        path = self._path_for(key)                      # 대상 파일
        if not path.exists():                           # 없으면 미적중
            return None
        try:
            payload = path.read_text(encoding="utf-8")  # 저장된 원문 읽기
        except OSError as exc:                          # 디스크 오류를 조용히 삼키지 않는다
            logger.warning("캐시 읽기 실패 key=%s: %s", key, exc)
            return None                                 # 다만 파이프라인은 계속 진행한다(재호출)
        logger.info("캐시 적중 key=%s", key)             # 적중 사실을 남긴다 (재현성 추적)
        return payload                                  # 저장된 응답 원문

    def put(self, key: str, payload: str) -> None:
        """응답 문자열을 캐시에 저장한다.

        입력  : key — cache_key() 결과, payload — 모델 응답 원문
        출력  : 없음
        부수효과: 디렉터리 생성 + 파일 쓰기. 쓰기 실패는 경고만 남기고 진행한다
                 (캐시는 최적화 계층이므로 실패가 추출 결과를 무효화하지 않는다).
        """
        if not self.enabled:                            # 비활성이면 저장하지 않는다
            return
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)   # 이 시점에 처음 디렉터리를 만든다
            self._path_for(key).write_text(payload, encoding="utf-8")  # 원문 그대로 저장
        except OSError as exc:                          # 실패를 삼키지 않고 기록한다
            logger.warning("캐시 쓰기 실패 key=%s: %s", key, exc)


class MemoryResponseCache(ResponseCache):
    """메모리 캐시. 테스트에서 디스크를 건드리지 않고 캐시 동작을 검증할 때 쓴다."""

    def __init__(self) -> None:
        """부모의 경로 설정은 쓰지 않고 dict 하나만 둔다."""
        super().__init__(cache_dir=DEFAULT_CACHE_DIR, enabled=True)  # 부모 초기화 (경로는 사용하지 않음)
        self._store: dict[str, str] = {}                # 키 → 응답 원문

    def get(self, key: str) -> str | None:
        """메모리에서 조회."""
        return self._store.get(key)                     # 없으면 None

    def put(self, key: str, payload: str) -> None:
        """메모리에 저장."""
        self._store[key] = payload                      # 덮어쓰기
