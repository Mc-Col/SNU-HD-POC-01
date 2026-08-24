# -*- coding: utf-8 -*-
"""모델 단계 자체 검증

    python fixtures/preprocess/test_models.py

이 파일이 지키는 것
    ① 1차는 싼 모델, 재시도는 상위 모델
    ② 같은 시도 번호 → 같은 모델 (확신도·난이도로 바뀌지 않는다, 철학 6)
    ③ 실행 로그에 모델 구성이 남는다 (재현성)
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.stdout.reconfigure(encoding="utf-8")

from src import models

ok = fail = 0


def check(label, got, want):
    global ok, fail
    if got == want:
        ok += 1
        print(f"  OK   {label}")
    else:
        fail += 1
        print(f"  실패 {label}\n         받음 {got!r}\n         기대 {want!r}")


print("\n[1] 시도 번호로 모델을 고른다")
check("1차(attempt 0) → luna", models.for_attempt(0).name, "gpt-5.6-luna")
check("재시도(attempt 1) → terra", models.for_attempt(1).name, "gpt-5.6-terra")
check("재시도(attempt 2) → terra", models.for_attempt(2).name, "gpt-5.6-terra")
check("음수도 1차로 본다", models.for_attempt(-1).name, "gpt-5.6-luna")

print("\n[2] 승격 여부를 알 수 있다 — 로그·화면에 표시")
check("1차는 승격 아님", models.for_attempt(0).is_escalated, False)
check("재시도는 승격", models.for_attempt(1).is_escalated, True)
check("사유가 있다", bool(models.for_attempt(1).why), True)

print("\n[3] 같은 시도 번호는 항상 같은 모델 (철학 6)")
check("반복 호출이 같다",
      [models.for_attempt(0).name for _ in range(3)],
      ["gpt-5.6-luna"] * 3)

print("\n[4] .env 로 덮어쓸 수 있다 — 모델이 바뀌어도 코드를 고치지 않는다")
os.environ["D2S_MODEL_TIER1"] = "test-model-a"
os.environ["D2S_MODEL_TIER2"] = "test-model-b"
check("tier1 덮어쓰기", models.for_attempt(0).name, "test-model-a")
check("tier2 덮어쓰기", models.for_attempt(1).name, "test-model-b")
check("요약도 따라온다", models.summary(),
      {"tier1": "test-model-a", "tier2": "test-model-b"})
del os.environ["D2S_MODEL_TIER1"], os.environ["D2S_MODEL_TIER2"]
check("환경변수를 지우면 기본값", models.for_attempt(0).name, "gpt-5.6-luna")

print("\n[5] 계약 — reread 가 attempt 를 받는다")
import inspect  # noqa: E402

from src.pipeline import NullParser, ParserModule  # noqa: E402
check("ParserModule.reread 에 attempt 있음",
      "attempt" in inspect.signature(ParserModule.reread).parameters, True)
check("NullParser 도 받는다",
      "attempt" in inspect.signature(NullParser.reread).parameters, True)

print("\n" + "=" * 62)
print(f"  통과 {ok} / 실패 {fail}")
print("=" * 62)
sys.exit(1 if fail else 0)
