# src/ui — 이종수 책임

**⑦ HITL — 사람이 확정한다 (Streamlit)**

- 입력: `DocumentResult`
- 출력: `승인 레코드 + 엑셀`

## 할 일

1. 좌측 원본 이미지 / 우측 항목 표 (화면정의서 기준)
2. 확인필요 항목에 마우스 오버 시 bbox 영역 하이라이트
3. 정상추출은 패스, 확인필요·N/A 는 개별 확인 강제
4. 필수 필드가 모두 해소되면 '검토 완료' 활성화 (DocumentResult.approvable)
5. [Cv 계산] 버튼 — RATED CV 가 N/A 일 때 공정조건으로 계산 제안
6. 엑셀 export

## 하지 말 것

- 검증 세션 전체 소요시간은 스톱워치로 수동 측정 (자동 로깅은 MVP 범위 외)

## 규칙

- 이 폴더 밖의 파일을 수정하지 않는다
- 계약은 `src/contracts.py` 를 import 해서 쓴다 (복사하지 않는다)
- 규칙·임계값은 `schema/*.yaml` 에서 읽는다
- fixture: `fixtures/ui/`
