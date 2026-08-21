# src/validate/format — 서경빈 선임

**⑤-a 형식·허용값 검증**

- 입력: `정규화된 값`
- 출력: `판정 + 위반 사유`

## 할 일

1. 필수 필드 충족 검사 (fields.yaml 의 required)
2. 타입·형식 검사 (숫자여야 하는 필드에 문자가 왔는지 등)
3. 위반 시 FailureKind.FORMAT 으로 표시하고 사유를 남긴다

## 하지 말 것

- 위반을 발견하면 재시도를 요청하지 말 것 — 형식 위반은 사람에게 넘긴다
- 범주형 허용값 리스트는 MVP 범위 외

## 규칙

- 이 폴더 밖의 파일을 수정하지 않는다
- 계약은 `src/contracts.py` 를 import 해서 쓴다 (복사하지 않는다)
- 규칙·임계값은 `schema/*.yaml` 에서 읽는다
- fixture: `fixtures/format/`
