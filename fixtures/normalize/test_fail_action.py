# -*- coding: utf-8 -*-
"""FAIL ACTION 변환 자체 검증 — 안전 필드다

    python fixtures/normalize/test_fail_action.py

이 필드가 왜 어려운가
    양식마다 두 가지 표기가 있고 **거의 똑같이 생겼으면서 결과가 정반대**다.

        [직접] Air Fails Valve to : Close   →  FAIL CLOSE
        [역전] Air to Open        : ☒       →  FAIL CLOSE   (같은 결과, 다른 경로)
        [역전] Air to Close       : ☒       →  FAIL OPEN
        [직접] Fail Position      : Open    →  FAIL OPEN

    값만 보면 `Close` 하나로 같다. **라벨을 봐야 방향이 정해진다.**

이 파일이 지키는 것
    ① 골든셋 11건에 실제로 나온 원문라벨 전부가 변환된다
    ② 라벨도 값도 방향을 알려주지 않으면 **변환하지 않는다**
       (모르는 채 방향을 정하면 안전 사양을 뒤집는다)
    ③ 다른 항목의 라벨(`Signal Increase to`)을 Fail Action 으로 읽지 않는다
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.stdout.reconfigure(encoding="utf-8")

from src import schema
from src.contracts import RawExtraction
from src.pipeline import DefaultNormalize

N = DefaultNormalize()
F = schema.get("actuator_fail_action")
ok = fail = 0


def run(raw_value, raw_label):
    ex = RawExtraction(field_key=F.key, raw_value=raw_value, raw_label=raw_label)
    return N.run(ex, F)


def check(label, got, want):
    global ok, fail
    if got == want:
        ok += 1
        print(f"  OK   {label}")
    else:
        fail += 1
        print(f"  실패 {label}\n         받음 {got!r}\n         기대 {want!r}")


print("\n[1] 직접 기재 — 골든셋 11건의 실제 원문라벨")
for lab, val, want in [
        ("Air Fails Valve to", "Close", "FAIL CLOSE"),      # d001 · d002
        ("Air Fails Valve to", "Open", "FAIL OPEN"),        # d003
        ("ACT'N Fail Position", "OPEN", "FAIL OPEN"),       # d004
        ("ACTUATOR Fail Position", "CLOSE", "FAIL CLOSE"),  # d005 · d010
        ("Fail Position", "Open", "FAIL OPEN"),             # d006
        ("ACTUATOR Failure Mode", "Close", "FAIL CLOSE"),   # d007
        ("Actuator Fail", "Open", "FAIL OPEN"),             # d008
]:
    v, tr = run(val, lab)
    check(f"{lab:<26}{val:<8}→ {want}", v, want)

print("\n[2] 역전 표기 — 값 자체가 ATO/ATC")
for val, want in [("Air-to-Open (ATO)", "FAIL CLOSE"), ("ATO", "FAIL CLOSE"),
                  ("AIR TO CLOSE", "FAIL OPEN"), ("ATC", "FAIL OPEN"),
                  ("FAIL LAST", "FAIL LAST")]:
    v, _ = run(val, "Actuator Type")
    check(f"{val:<20}→ {want}", v, want)

print("\n[3] 모르면 변환하지 않는다 — 안전 사양을 추측으로 뒤집지 않는다")
for lab, val in [("Signal Increase to", "Close"),   # 신호 방향. Fail Action 이 아니다
                 ("Air to Actuator", "Open"),       # 역전 라벨이지만 사전에 없다
                 ("Flow to", "Open"),
                 ("", "Close")]:                    # 라벨 없음
    v, tr = run(val, lab)
    check(f"{(lab or '(라벨없음)'):<22}{val:<8}→ 원문 보존", v, val)
    assert any("규칙 미적용" in t for t in tr), f"사유가 없다: {tr}"

print("\n[4] transform_trace 에 근거가 남는다")
v, tr = run("Close", "Air Fails Valve to")
check("결과", v, "FAIL CLOSE")
check("원문라벨이 근거에 있다", any("직접 기재" in t for t in tr), True)
check("원문값이 근거에 있다", any("Close" in t for t in tr), True)
print("      " + " → ".join(tr))

print("\n[5] 값이 없으면 만들지 않는다")
check("None", run(None, "Fail Position"), (None, []))
check("빈 문자열", run("", "Fail Position"), (None, []))

print("\n" + "=" * 62)
print(f"  통과 {ok} / 실패 {fail}")
print("=" * 62)
sys.exit(1 if fail else 0)
