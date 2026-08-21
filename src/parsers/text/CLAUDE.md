# src/parsers/text — 서경빈 선임

**③-a TEXT PARSER — 헤더를 표준 컬럼에 붙인다**

- 입력: `엑셀 시트 또는 텍스트 PDF 페이지`
- 출력: `RawExtraction[]`

## 할 일

1. 셀·텍스트 블록을 스캔해 라벨 후보 추출
2. 헤더 텍스트 → 표준 컬럼명 매핑 (schema/fields.yaml 의 name + aliases 사용)
3. 라벨 우측·하단 셀에서 값 추출 → source_locator 에 'Sheet1!C7' 형태로 기록
4. raw_label 에 문서에 적힌 항목명을 그대로 담는다 ← 유사표현 사전이 여기서 자란다
5. 매핑 실패 라벨은 unmapped 로 수집해 로그에 남긴다

## 하지 말 것

- 셀 좌표로 매핑하지 말 것 — 벤더 양식의 디테일이 바뀐다. 헤더 텍스트 기준으로.
- aliases 를 코드에 넣지 말 것. schema/fields.yaml 에만 둔다

## 규칙

- 이 폴더 밖의 파일을 수정하지 않는다
- 계약은 `src/contracts.py` 를 import 해서 쓴다 (복사하지 않는다)
- 규칙·임계값은 `schema/*.yaml` 에서 읽는다
- fixture: `fixtures/text/`
