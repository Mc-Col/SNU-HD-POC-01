# -*- coding: utf-8 -*-
"""`.env` 로딩 — 진입점이 여러 개라 한 곳에 모은다

    from src import env
    env.load()

왜 필요한가 (2026-08-24)
─────────────────────────────────────────────────────────────
화면에서 VLM 을 돌렸더니 이렇게 났다.

    OpenAIError: Missing credentials. Please pass an api_key ...

`eval/harness.py` 는 `load_dotenv()` 를 직접 불러서 잘 돌았는데 `app.py` 는
부르지 않았다. 진입점이 여러 개(화면·하네스·스크립트)인데 각자 기억해야 하는
구조였던 것이다. **잊을 수 있는 것은 잊힌다** — 그래서 한 곳으로 모으고,
잊었을 때의 오류 메시지를 사람이 읽을 수 있게 바꾼다.
"""
from __future__ import annotations

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(ROOT, ".env")

_loaded = False


def load(force: bool = False) -> bool:
    """`.env` 를 읽는다. 이미 읽었으면 다시 읽지 않는다. → 키가 있는가.

    `python-dotenv` 가 없어도 죽지 않는다 — 직접 파싱한다. 환경변수를 이미
    설정해 둔 사람의 경로를 막지 않기 위해 **기존 값을 덮어쓰지 않는다.**
    """
    global _loaded
    if _loaded and not force:
        return has_key()
    _loaded = True

    if not os.path.exists(ENV_PATH):
        return has_key()
    try:
        from dotenv import load_dotenv
        load_dotenv(ENV_PATH)
    except ImportError:
        # dotenv 미설치 — 최소 파서로 대신한다
        with open(ENV_PATH, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    return has_key()


def has_key() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


MISSING = (
    "API 키를 찾지 못했습니다.\n\n"
    "프로젝트 루트의 `.env` 에 아래 한 줄이 있어야 합니다:\n\n"
    "    OPENAI_API_KEY=sk-...\n\n"
    "이미 있는데도 이 메시지가 나오면 스트림릿을 재시작하세요 — "
    "`.env` 는 프로세스가 시작할 때 읽습니다.\n"
    "키 없이 화면을 보려면 `VLM 으로 판독` 을 끄거나 "
    "`합성 픽스처로 화면 보기` 를 쓰세요."
)


def require_key() -> None:
    """키가 없으면 사람이 읽을 수 있는 메시지로 멈춘다."""
    load()
    if not has_key():
        raise RuntimeError(MISSING)
