# D2S Ingestion Engine — PoC

**VLM 기반 연속공정 디지털트윈 Asset master agent 1단계 개발 PoC**

비정형 설비 문서에서 컨트롤밸브 기준정보 30필드를 추출·검증해 마스터 스키마 엑셀로
내보낸다. 사람이 최종 확정한다.

## 처음 오셨다면 — 5단계

| # | 할 일 | 참고 |
|---|---|---|
| 1 | GitHub Desktop 설치 후 이 저장소를 Clone | `docs/GIT_GUIDE.md` |
| 2 | **`setup.bat`** 더블클릭 | 자동 설치 |
| 3 | **`check_env.bat`** 더블클릭 → 전부 `OK` 확인 | 실패 시 화면에 조치 안내 |
| 4 | 문서 원본을 `raw_file/` 에 넣기 | 별도 전달 |
| 5 | `docs/roles/` 에서 **자기 이름 파일**을 읽기 | 내 역할 |

## 작업 시작

```
claude
```

Claude 에게:

```
docs/roles/<내 이름>.md 와 CLAUDE.md 를 읽고 내 담당 모듈을 파악해줘.
fixtures 로 검증 가능한 최소 구현부터 시작하자.
```

## 구조

```
schema/       fields.yaml (30필드 정의) · rules.yaml (변환 규칙)   ← 서경빈 선임
src/
  contracts.py     계약 3개 — 이종수 책임만 수정
  triage/ router/  ① ②                                          ← 강민호 책임
  parsers/vlm/     ③-b                                           ← 강민호 책임
  parsers/text/    ③-a                                           ← 서경빈 선임
  normalize/       ④                                             ← 이종수 책임
  validate/format/ ⑤-a                                           ← 서경빈 선임
  validate/domain/ ⑤-b                                           ← 이종수 책임
  state/ ui/       ⑥ ⑦                                           ← 이종수 책임
eval/         평가 하네스 (홀드아웃 잠금 · --no-vlm)
fixtures/     모듈별 입력·기대출력 샘플
docs/         ARCHITECTURE · PRINCIPLES · GIT_GUIDE · roles
runs/         실행 로그 (Git 제외)
raw_file/     문서 원본 (Git 제외)
```

## 규칙 요약

- **자기 모듈 폴더만 수정한다**
- 규칙은 코드가 아니라 `schema/*.yaml` 에
- 모르면 값을 만들지 않는다 (`state=NA` + `note`)
- `raw_file/` 과 `.env` 는 Git 에 올리지 않는다

자세한 내용은 `CLAUDE.md` 와 `docs/PRINCIPLES.md`.

## 일정

| 날짜 | 내용 |
|---|---|
| 8/21 (금) | 스키마 · 계약 · 라벨링 착수 |
| 8/22~24 | 라벨링 완료 + MVP 수직 슬라이스 |
| 8/24~25 | 모듈 병렬 확장 |
| 8/26 (수) | 통합 → 홀드아웃 평가 |
| **8/27 (목)** | **발표** |
