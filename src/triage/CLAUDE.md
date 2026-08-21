# src/triage — 강민호 책임

**① TRIAGE — 이 파일에 데이터가 있나**

- 입력: `파일 경로`
- 출력: `TriageResult`

## 할 일

1. 확장자·파일명 패턴 검사 (태그번호 정규식, schedule/summary/list 키워드)
2. 구조 통계 수집 (페이지 수, 텍스트 길이, 표·행 개수, 시트 수) → stats 에 담기
3. 첫 페이지·첫 시트 경량 판독으로 document_class 판정
4. datasheet_embedded 의심 시 태그번호 출현 페이지 탐색 → targets 채우기
5. out_of_scope 로 판정하면 reason 을 반드시 채운다

## 하지 말 것

- 예외를 던지지 말 것 — 미지원 포맷은 UNSUPPORTED 로 기록하고 정상 반환
- MVP 는 1파일 = 1자산 전제. expected_tag_count 는 1로 둔다

## 규칙

- 이 폴더 밖의 파일을 수정하지 않는다
- 계약은 `src/contracts.py` 를 import 해서 쓴다 (복사하지 않는다)
- 규칙·임계값은 `schema/*.yaml` 에서 읽는다
- fixture: `fixtures/triage/`
