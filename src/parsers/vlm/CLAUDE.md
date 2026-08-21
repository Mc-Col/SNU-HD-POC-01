# src/parsers/vlm — 강민호 책임

**③-b VLM PARSER — 이미지에서 값과 위치를**

- 입력: `페이지 이미지 + 필드 정의`
- 출력: `RawExtraction[]`

## 할 일

1. 전처리 — 기울기 보정, 해상도 정규화
2. schema/fields.yaml 의 name·desc·aliases 를 프롬프트에 주입
3. 값 + bbox + confidence 를 함께 요구한다 (bbox 가 UI 하이라이트의 근거)
4. 문서에 없으면 raw_value=None 으로 정직하게 반환
5. 재시도 요청 시 bbox 크롭만 재판독

## 하지 말 것

- 재시도 프롬프트에 '값이 틀렸다'고 쓰지 말 것 — 환각을 유도한다.
-   올바른 문구: '이 영역에 문자 그대로 무엇이 적혀 있는지 보고하라. 같은 값이면 같다고 답하라'
- 없는 값을 추정해서 만들지 말 것

## 규칙

- 이 폴더 밖의 파일을 수정하지 않는다
- 계약은 `src/contracts.py` 를 import 해서 쓴다 (복사하지 않는다)
- 규칙·임계값은 `schema/*.yaml` 에서 읽는다
- fixture: `fixtures/vlm/`
