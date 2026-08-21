# src/state — 이종수 책임

**⑥ STATE — 셋 중 하나로 확정한다**

- 입력: `검증 결과`
- 출력: `FieldRecord`

## 할 일

1. fields.yaml 의 threshold·safety 로 상태 결정 (AUTO / REVIEW / NA)
2. note 를 생성한다 — AUTO 외 상태는 note 가 비면 계약 위반으로 예외 발생
3. 안전·식별 필드는 AUTO 여도 사람 확인 필요 표시
4. FieldRecord.validate() 를 반드시 호출

## 하지 말 것

- 임계값을 코드에 하드코딩하지 말 것 — fields.yaml 에서 읽는다

## 규칙

- 이 폴더 밖의 파일을 수정하지 않는다
- 계약은 `src/contracts.py` 를 import 해서 쓴다 (복사하지 않는다)
- 규칙·임계값은 `schema/*.yaml` 에서 읽는다
- fixture: `fixtures/state/`
