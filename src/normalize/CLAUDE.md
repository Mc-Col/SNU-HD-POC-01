# src/normalize — 이종수 책임

**④ NORMALIZE — 표준값으로 바꾼다**

- 입력: `RawExtraction[]`
- 출력: `표준값 + transform_trace`

## 할 일

1. 도메인 규칙 적용 — ATO → Fail Close, ATC → Fail Open (역전 매핑)
2. 변환 과정을 transform_trace 에 단계별로 기록
3. schema/rules.yaml 을 읽어 적용 (코드에 규칙을 넣지 않는다)

## 하지 말 것

- 단위 변환은 MVP 범위 외 — 원문 표기 그대로 보존한다 (To-be)
- 범주형 허용값 정규화도 MVP 범위 외

## 규칙

- 이 폴더 밖의 파일을 수정하지 않는다
- 계약은 `src/contracts.py` 를 import 해서 쓴다 (복사하지 않는다)
- 규칙·임계값은 `schema/*.yaml` 에서 읽는다
- fixture: `fixtures/normalize/`
