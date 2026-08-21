# src/validate/domain — 이종수 책임

**⑤-b 도메인·물리 제약 검증**

- 입력: `정규화된 값 세트`
- 출력: `판정 + 위반 사유`

## 할 일

1. FAIL ACTION ↔ ACTUATOR TYPE 정합성 교차검증
2. 유효 ANSI 클래스 / 표준 배관 규격 검사
3. 위반 시 FailureKind.CONSTRAINT — 재시도하지 않고 사람에게

## 하지 말 것

- Cv 물리 교차검증은 루프에서 제외 — UI 의 [Cv 계산] 버튼 기능으로 별도 구현
- 제약 위반에 재시도를 걸지 말 것 (환각 제조)

## 규칙

- 이 폴더 밖의 파일을 수정하지 않는다
- 계약은 `src/contracts.py` 를 import 해서 쓴다 (복사하지 않는다)
- 규칙·임계값은 `schema/*.yaml` 에서 읽는다
- fixture: `fixtures/domain/`
