# src/router — 강민호 책임

**② ROUTER — 어느 파서로 보낼지**

- 입력: `TriageResult + 파일`
- 출력: `ParserType + 처리 단위 목록`

## 할 일

1. 엑셀 계열 → 포맷별 리더 분기 (xlsx/xlsm 는 openpyxl, xls 는 xlrd)
2. PDF 는 텍스트 레이어를 탐침 — 추출 가능 텍스트 비율이 임계 이상이면 PDF_TEXT, 아니면 VLM
3. tif → 다중 페이지 분해 후 VLM
4. 판정 근거를 남긴다 (텍스트 비율 등)

## 하지 말 것

- 확장자만 보고 PDF 를 VLM 으로 보내지 말 것 — 텍스트 PDF 가 30% 다
- 탐침 임계값은 하드코딩하지 말고 상수로 분리

## 규칙

- 이 폴더 밖의 파일을 수정하지 않는다
- 계약은 `src/contracts.py` 를 import 해서 쓴다 (복사하지 않는다)
- 규칙·임계값은 `schema/*.yaml` 에서 읽는다
- fixture: `fixtures/router/`
