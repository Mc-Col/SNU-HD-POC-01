# VLM 문서 추출 정합성 — 외부 레퍼런스 조사

조사일 2026-08-24 · 조사자 강민호 책임 (③-b VLM Parser)
대상: OpenAI / Anthropic 계열 VLM 으로 문서에서 값을 뽑을 때 **정합성을 올리는 방법**에 관한
논문 · 공식 문서 · 공개 저장소

이 문서는 판단의 근거만 모은다. 결정은 `ARCHITECTURE.md`, 경과는 `change_log.md` 에 남긴다.

---

## 요약

1. **우리 설계의 큰 뼈대는 외부 근거와 일치한다.** 페이지를 먼저 좁히는 것(§4.2)과 기하
   전처리가 정확도에 가장 크게 기여한다는 실측이 있고(각 16.8~24.0%p, 6.2~16.3%p),
   "텍스트 레이어로 글자를 보증한다"(§4.4)는 판단은 신뢰도 신호 연구에서 **로그확률보다
   강한 단일 신호**로 확인됐다(AUC 0.896 vs 0.880).
2. **가장 큰 구멍은 `confidence` 다.** 지금 우리는 모델이 스스로 적은 숫자를 0.90/0.95
   임계에 그대로 걸고 있다. 자기보고 확신도는 체계적으로 과신하며, 구조화 JSON 출력에서는
   로그확률조차 99.4~100% 가 0.999 위로 포화한다. 이 임계는 현재 **측정되지 않은 숫자**다.
3. **대안은 이미 우리 손에 있다.** 다신호 합성(추출값이 텍스트 레이어에 문자 그대로 있는가 ·
   크롭 재판독 일치 · bbox 선명도 · 라벨 매칭 · 규칙 통과)으로 바꾸면 AUC 0.705 → 0.928,
   커버리지 80% 에서 정확도 99.1% 라는 보고가 있다. 필요한 재료는 `preprocess.probe_pages`,
   `reread()`, `fields.yaml` 로 **전부 이미 구현돼 있다.**

---

## 1. 조사 범위와 한계

- 검색 시점 2026-08-24. arXiv · 공급자 공식 문서 · GitHub 를 대상으로 했다.
- 논문 수치는 본문 표에서 가져왔으나 **일부는 PDF 요약 도구를 경유했다.** 의사결정에 직접
  쓸 값은 표에 `검증` 열로 표시했다. `2차`로 표시된 것은 원문 표를 직접 재확인해야 한다.
- 벤치마크 점수로 모델을 고르지 않는다 — §5 에 이유가 있다.

---

## 2. 우리 설계를 지지하는 근거

### 2.1 페이지를 먼저 좁히는 것이 최대 레버 (§4.2 지지)

산업 KYC 워크플로의 스캔 금융문서 파이프라인 실측
([arXiv:2604.26462](https://arxiv.org/abs/2604.26462)):

| 구성 | 필드 정확도 |
|---|---|
| 전체 문서를 대형 VLM 에 통째로 투입 | 55.38% |
| EasyOCR + 소형 VLM 다단계 | 75.20% |
| PaddleOCR + MiniCPM-o-2.6 다단계 | **87.27%** |

모듈별 제거 실험(ablation):

| 제거한 단계 | 정확도 하락 |
|---|---|
| **페이지 검색(retrieval)** | **16.8 ~ 24.0%p** |
| 이미지 전처리(방향·기울기·대비) | 6.2 ~ 16.3%p |
| 구조화 프롬프트 | 소폭 개선 (설정 의존) |

같은 방향의 별도 보고 — 다단계 파이프라인이 전체 문서 직접 투입 대비 **필드 정확도 8.8배,
GPU 비용 0.7%, 지연 92.6% 감소** ([arXiv:2510.23066](https://arxiv.org/abs/2510.23066)).

**우리에게 주는 뜻**
- §4.2 의 "격자 1장으로 후보를 고르고 2~4회로 끝낸다"는 비용 절감 장치가 아니라 **정확도
  장치**였다. 이 사실을 발표 자료에 근거와 함께 넣을 수 있다.
- `pick_latest_spec()` 이 못 가릴 때 `None` 을 돌려주는 설계(=사람에게 넘김)는 잘못 고른
  페이지가 16~24%p 를 깎는다는 점에서 정당하다.
- 세 번째 줄이 눈에 걸린다 — **구조화 프롬프트를 뺐더니 오히려 좋아진 설정이 있었다.**
  우리 `SYSTEM` 은 상당히 긴 구조화 지시다. §4.4 항목으로 A/B 해볼 값어치가 있다.

### 2.2 기하 전처리는 켜는 게 맞다 (`deskew` 기본값 재검토)

위 표에서 전처리 제거 시 6.2~16.3%p 하락. 우리 실측은 스캔 758건 중 **71.5% 가 0.5도
초과 기울기**이고 보정 시 글줄 투영 점수 중앙값 159% 개선이다(`openai_vlm.py` docstring).

지금 `VlmParser(deskew=False)` 가 기본이다. "검증되지 않은 동작을 기본으로 만들지 않는다"는
판단 자체는 옳다. 다만 **골든셋 30건에서 deskew on/off A/B 를 돌려 기본값을 확정해야 한다.**
외부 근거는 켜는 쪽을 가리킨다.

### 2.3 "텍스트 레이어가 글자를 보증한다"는 §4.4 는 신뢰도 신호로도 최강급

문서 필드 추출용 신뢰도 엔진 논문 ExtractConf
([arXiv:2606.24420](https://arxiv.org/abs/2606.24420), DocILE 테스트셋):

| 방법 | AUC | ECE |
|---|---|---|
| B1 로그확률 평균만 | 0.705 | 0.245 |
| B3 self-consistency 5회 | 0.744 | — |
| M1 로그확률 + 엔트로피 | 0.880 | 0.180 |
| **M4 OCR 근거 신호만** | **0.896** | 0.197 |
| M6 전체 결합 | **0.928** | 0.199 |
| M7 = M6 + isotonic 보정 | 0.926 | **0.034** |

저자 설명: *"추출 오류는 문서가 원인이지 모델이 원인이 아니다. OCR 신뢰도는 실패 원인을 직접
측정하고, 로그확률은 그것과 무관한 결과를 측정한다."* 프론티어 LLM 은 **읽을 수 없는 원본을
읽으면서도 OCR 노이즈에 높은 로그확률을 준다.**

M4 의 핵심 신호는 *"추출된 값이 원본 OCR 텍스트에 문자 그대로 등장하는가"* 라는 이진 지표다.
이것은 §4.4 가 이미 정한 역할 분담 — **VLM 이 필드를 정하고 텍스트 레이어가 글자를 보증한다**
— 와 정확히 같다.

**우리에게 주는 뜻**: 우리 코퍼스에서 PDF 는 페이지 기준 텍스트 68.3%, 정비보고서는 112건 중
111건이 텍스트 레이어를 갖는다. 이 신호는 **추가 API 비용 0원**으로 지금 붙일 수 있다.

### 2.4 크롭 재판독은 검증된 패턴 (`reread()` 지지)

- olmOCR ([arXiv:2502.18443](https://arxiv.org/abs/2502.18443)) — 실패 페이지는 앵커를
  다시 뽑아 재시도하고, 끝내 실패하면 순수 텍스트 추출로 폴백한다.
- CropVLM ([arXiv:2511.19820](https://arxiv.org/abs/2511.19820)) — 관련 영역으로
  "확대"하면 장면 텍스트·문서 분석 같은 미세 판독 과제 성능이 오른다.
- 실무 정리(awesome-ocr-2026) — *"신뢰도 낮은 구역만 Claude/GPT-4o 로 재처리"* 를 표준
  배포 패턴으로 든다.

우리 `reread()` 는 bbox 크롭 + 중립 문구(§4.5)로 이미 이 형태다. **바꿀 것 없다.**

### 2.5 근거 없으면 비운다(`absence_reason`)는 abstention 문헌과 일치

선택적 예측(selective prediction)은 커버리지와 위험을 맞바꾸는 표준 프레임이다. 우리
3-상태 모델(AUTO/REVIEW/NA)과 `absence_reason` 3분류(`no_evidence` / `unreadable` /
`checkbox_ambiguous`)는 이 프레임의 구현이고, 특히 **"왜 없는지"를 나누는 것은 문헌보다
앞서 있다** — 대부분의 논문은 abstain 여부만 다룬다.

---

## 3. 우리 설계에 구멍이 있는 곳

### 3.1 [최우선] 자기보고 `confidence` 를 임계에 그대로 거는 것

**현황.** `SYSTEM` 5항이 모델에게 0.0~1.0 확신도를 적게 하고, `ARCHITECTURE.md` §2 가
0.90 / 0.95 임계로 AUTO·REVIEW 를 가른다.

**문제.** 세 갈래 근거가 모두 같은 방향이다.

1. **자기보고 확신도는 보정돼 있지 않다.** 낮은 정확도 구간에서 높은 확신도를 보고하는
   체계적 과신이 관찰되며, 점수가 80/90/100 같은 몇 개 값에 뭉치는 **확신도 포화**가
   보고됐다 ([arXiv:2412.14737](https://arxiv.org/abs/2412.14737),
   [arXiv:2509.25532](https://arxiv.org/abs/2509.25532)).
2. **구조화 JSON 출력에서는 로그확률도 못 쓴다.** VERDI
   ([arXiv:2605.11334](https://arxiv.org/abs/2605.11334)) 는 SummEval·FEVER 에서
   **로그확률의 99.4~100% 가 0.999 를 넘어 포화**해 사실상 상수가 된다고 보고한다.
   우리는 `response_format={"type":"json_object"}` 를 쓰므로 정확히 이 조건이다.
3. **VLM OCR 오류 회피 벤치마크에서 자기보고류가 하위권이다**
   ([arXiv:2511.19806](https://arxiv.org/abs/2511.19806), 4개 벤치마크 평균 abstention
   accuracy):

   | 방법 | 정확도 |
   |---|---|
   | VLM Judge | 0.722 |
   | Token Probability | 0.669 |
   | Self Consistency | 0.646 |
   | R-Tuning | 0.628 |
   | **Ask for Calibration (자기보고)** | **0.604** |
   | Contextual Lens | 0.586 |
   | SVAR | 0.573 |
   | **Prompt to Abstain** | **0.427** |

   같은 논문이 training-free 방법 중에서는 self-consistency 가 최고(한 모델 설정 기준
   68.0%)라고도 적는다. 표 평균과 조건이 다르므로 **인용 시 조건을 함께 적어야 한다.**

**결론.** 0.90 / 0.95 는 지금 상태로는 **의미가 확인되지 않은 숫자**다. 임계를 손보는 게
아니라 **확신도를 만드는 방식을 바꿔야 한다.**

**대안 — 다신호 합성.** ExtractConf 가 결합한 신호군을 우리 자산에 대응시키면:

| ExtractConf 신호군 | 우리 대응 | 지금 있는가 |
|---|---|---|
| G2 값이 원본 OCR 텍스트에 문자 그대로 있는가 | `preprocess.probe_pages()` + `pdf_text` 로 페이지 텍스트 확보 후 대조 | 재료 있음, 대조 미구현 |
| G3 서로 다른 호출 간 값 일치 | `reread()` 크롭 재판독 결과와 1차 값 비교 | **구현됨** |
| G4 라벨 근접·bbox 선명도(라플라시안) | `raw_label` ↔ `fields.yaml` aliases 매칭, bbox 크롭의 라플라시안 분산 | 라벨 있음, 선명도 미구현 |
| G5 필드 유형 인코딩 | `fields.yaml` 의 타입 | 있음 |
| G1 로그확률·엔트로피 | (구조화 출력에서 포화 — 우선순위 낮음) | 미사용 |

여기에 우리에게만 있는 **무료 신호**가 하나 더 있다 — ⑤ 형식 검증 통과 여부. 비용 사다리
앞칸(무료 규칙 검증)의 결과를 확신도 신호로 되먹이면 추가 호출 없이 신호가 는다.

보정은 골든셋으로 한다. 30문서 × 28필드 ≈ 840 관측이므로 **필드 유형별로 묶으면** isotonic
또는 로지스틱 보정이 가능하다. ExtractConf 는 isotonic 적용으로 ECE 0.199 → 0.034 를
얻었다.

**임계는 정하는 게 아니라 그리는 것이다.** 커버리지-정확도 곡선을 그려 "자동 확정 비율 X%
일 때 정확도 Y%" 를 보고 정한다. ExtractConf 보고값은 커버리지 80% 에서 정확도 99.1%
(무선별 기준 73.3% 대비 +25.8%p).

### 3.2 28필드 1콜 — 필드 수가 늘면 떨어진다

ExStrucTiny ([arXiv:2602.12203](https://arxiv.org/abs/2602.12203)) 는 스키마 가변
구조화 추출에서 **뽑을 값의 개수가 늘수록 성능이 떨어진다**고 보고한다(오픈소스 VLM 에서
뚜렷, 폐쇄형은 상대적으로 안정: Gemini-2.5-Pro 79.5 ANLS vs Qwen2.5-VL-72B 61.4 ANLS).
스키마를 준 질의는 평문 질의보다 약 3배 많은 엔티티를 요구해 더 어렵다고도 적는다.

**우리에게 주는 뜻**: 우리는 GPT 계열(폐쇄형)이므로 최악은 아니다. 그리고 **비교 실험이
공짜다** — `VlmParser(only_mvp=True)` 로 9필드, `False` 로 28필드를 같은 골든셋에 돌려
MVP 9필드의 정확도가 달라지는지 보면 된다. 달라지면 필드를 그룹으로 쪼개는 근거가 된다.

### 3.3 재현성 — 철학 6 이 아직 API 호출에서 보장되지 않는다

`_ask()` 는 `temperature` 도 `seed` 도 넘기지 않는다. 그리고 **넘겨도 부족하다** —
공유 추론 엔드포인트의 비결정성은 난수가 아니라 **배치 크기에 따라 커널 수치 경로가 달라지는
것**이 원인이라는 분석이 있다
([Thinking Machines Lab](https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/)).
temperature 0 + 고정 seed 로도 부하가 다르면 결과가 갈린다.

**결론**: `cache.py` 의 응답 캐시가 철학 6 의 **유일한 실질 보증**이다. 지금 기본이 꺼져
있다(`cache=None`). 평가 하네스 경로에서는 **캐시를 기본 ON 으로 두는 것이 맞다.**
`temperature=0` · `seed` 명시는 값싼 추가 보험으로 함께 넣는다(보증이 아니라 분산 축소).

### 3.4 JSON 강제의 부작용 — 필드 순서와 구조화 비용

- 제약 디코딩은 확률 분포를 재정규화하면서 품질을 떨어뜨릴 수 있고, 모델이 학습 중 거의 보지
  못한 토큰 경로를 강제당한다는 분석이 있다
  ([JSONSchemaBench, arXiv:2501.10868](https://arxiv.org/abs/2501.10868)).
- **스키마의 필드 순서가 성능에 영향을 준다** — 결론 필드가 근거 필드보다 앞에 오면 모델이
  근거를 적기 전에 답을 확정해 추론이 나빠진다.

**우리 JSON 은 `raw_value` 가 맨 앞이고 `note` 가 맨 뒤다.** 판독 근거(`raw_label`,
`row_text`, `bbox`)를 값보다 **앞에** 두는 순서로 바꿔 A/B 할 가치가 있다. 프롬프트만
바꾸면 되고 `PROMPT_VERSION` 을 올리면 캐시도 안전하게 갈린다.

### 3.5 Claude Citations 는 우리 주 경로에 쓸 수 없다 (확인 완료)

[공식 문서](https://platform.claude.com/docs/en/build-with-claude/citations) 확인 결과:

- **이미지 인용 미지원.** *"PDFs that are scans of documents and do not contain
  extractable text are not citable."* → tif 734건(71.9%)과 스캔 PDF 는 대상 밖.
- **구조화 출력과 병용 불가.** citations 를 켠 문서에 `output_config.format` 을 함께
  주면 **400 에러**. 우리는 JSON 스키마 출력이 전제다.
- 인용 단위는 PDF=문장 단위 + 페이지 번호(1-indexed), 평문=문자 위치.
- `cited_text` 는 출력 토큰에 계상되지 않는다(비용 이점).

**결론: 도입하지 않는다.** 우리의 근거 제시는 이미 **bbox** 가 맡고 있고, 스캔 문서에서는
bbox 가 citations 보다 강하다. 다만 xlsx·텍스트 PDF 경로(11.6% + 텍스트 페이지)에서는
"문장 단위 근거"를 값싸게 얻는 수단으로 남겨둘 수 있다 — 단, 그 경로는 애초에 텍스트 파서
담당이다.

---

## 4. 도입 후보 — 우선순위

| # | 기법 | 근거 | 예상 효과 | 구현 비용 | 모듈 |
|---|---|---|---|---|---|
| 1 | **다신호 confidence** (텍스트 verbatim 대조 + 재판독 일치 + 라벨 매칭 + 선명도 + 규칙 통과) | ExtractConf AUC 0.705→0.928 | 임계가 처음으로 의미를 가짐. REVIEW 큐 크기가 근거 있는 값이 됨 | 중 (API 추가 호출 0) | `vlm` + `validate` |
| 2 | **골든셋 커버리지-정확도 곡선으로 임계 결정** | 커버리지 80% → 99.1% 보고 | 0.90/0.95 를 측정된 숫자로 대체 | 소 | `eval/harness.py` |
| 3 | **텍스트 앵커링** — 텍스트 레이어 있는 페이지에서 좌표+텍스트 블록을 프롬프트에 동봉 (상한 6,000자) | olmOCR: 앵커링 프롬프트가 환각을 뚜렷이 줄임 | §10 의 "VLM+텍스트" 칸의 실제 구현이 됨 | 중 | `vlm` |
| 4 | **`deskew` 기본값 A/B 후 확정** | 전처리 제거 시 6.2~16.3%p 하락 | 기본값 근거 확보 | 소 | `vlm` |
| 5 | **9필드 vs 28필드 1콜 A/B** | ExStrucTiny: 값 개수↑ → 성능↓ | 필드 분할 필요 여부 판정 | 소 (`only_mvp` 기존) | `vlm` |
| 6 | **JSON 필드 순서 A/B** (근거를 값 앞으로) | 결론-우선 스키마가 추론을 저해 | 프롬프트 한 줄 | 소 | `vlm` |
| 7 | **캐시 기본 ON + temperature/seed 명시** | 배치 비불변성으로 API 는 비결정적 | 철학 6 실질 보증 | 소 | `vlm` |
| 8 | **평가 Agent = 채점자 분리** | 채점 LLM 80.5% vs 추출 에이전트 73.3%, 난이도 높은 케이스 76.6% vs 61.1% ([arXiv:2510.19334](https://arxiv.org/abs/2510.19334)) | ⑤ 평가Agent 의 존재 근거. 열린 추출보다 후보 검증이 쉽다 | 중 | `validate` |

1·2 번을 먼저 한다. 나머지는 전부 **A/B 한 칸**이고 골든셋만 있으면 돌아간다.

---

## 5. 채택하지 않기를 권하는 것

| 후보 | 이유 |
|---|---|
| **다중 모델 앙상블 / Consensus Entropy** ([arXiv:2504.11101](https://arxiv.org/abs/2504.11101)) | 성능은 확실하다(라우팅 +6.5%, 5모델에서 최적, 7.3% 만 강모델로 승급). 그러나 우리는 **`.env` 에 `OPENAI_API_KEY` 하나뿐**이고, 모델을 늘리면 비용·재현성·발표 범위가 동시에 늘어난다. **후속 과제로 기록만 한다.** |
| **동일 모델 self-consistency (n회 샘플링 투표)** | abstention accuracy 0.646 로 약하고, 샘플링을 켜는 순간 철학 6 과 정면 충돌한다. 우리에겐 **크롭 재판독(이미 구현)** 이 더 싸고 조건에 맞는 교차검증이다. |
| **MinerU / Docling / Marker 등 오픈소스 파서로 교체** | 이들은 **문서 → 마크다운** 변환기다. 우리 과제는 "28개 지정 필드를 어느 칸에서 뽑았는지" 이고 마크다운 품질이 아니다. 다만 **텍스트 앵커 생성기**로는 쓸 수 있다(후보 3 의 대안 구현). |
| **Claude Citations** | §3.5 — 스캔 이미지 인용 미지원 + 구조화 출력 병용 시 400. |
| **공개 벤치마크 점수로 모델 선택** | OmniDocBench 는 포화 상태다. GLM-OCR·PaddleOCR-VL-1.5 가 94% 를 넘겼고 Gemini 3 Pro 가 90.3% 인데, 벤치 자체가 1,355페이지 · 9개 문서 유형이다([LlamaIndex](https://www.llamaindex.ai/blog/omnidocbench-is-saturated-what-s-next-for-ocr-benchmarks)). **1986년 팩스 헤더가 붙은 1비트 150DPI 스캔은 어떤 공개 벤치에도 없다.** 우리 골든셋 30건이 유일하게 의미 있는 척도다. |

---

## 6. 우리 코퍼스와 직접 관련된 개별 사실

- **손글씨.** GPT-4o 는 RIMES 에서 CER 1.69%, GPT-4o-mini 는 IAM 에서 CER 1.71% 로
  전용 HTR 모델을 넘어선다([arXiv:2503.15195](https://arxiv.org/abs/2503.15195)).
  우리 정비보고서의 수기 기재는 **모델 능력 문제가 아니라 칸이 비어 있는 문제**라는 §4.3 의
  판단과 맞다.
- **체크박스·양식.** 양식 처리 벤치마크에서 Claude 계열이 GPT·Gemini 대비 10%p 이상 약하고
  이미지 크기 제약으로 일부 양식을 아예 처리 못 했다는 보고가 있다
  ([arXiv:2604.16504](https://arxiv.org/abs/2604.16504), 검증 2차). 우리는 OpenAI
  경로이므로 당장 영향은 없으나, 모델 다변화 시 **체크박스 fixture 4종으로 먼저 거른다.**
- **해상도.** Anthropic 은 장변 1568px 초과 시 축소한다(우리 `TARGET_LONG_EDGE_PX`
  주석과 §8 격자 결정의 근거). 실무 글에서는 8pt 스캔 텍스트에 2500px 가 값어치 있다는
  주장도 있으나 **1차 출처 미확인**이다. 우리 코퍼스는 전부 1753px 이라 현재 무영향.
- **olmOCR 렌더 해상도.** 파인튜닝 1024px, GPT-4o 라벨링 2048px(당시 상한). 우리
  2576px 은 이보다 크다 — 비용 대비 효과를 골든셋에서 한 번 재볼 값어치가 있다.
- **반복 붕괴.** olmOCR 의 최빈 실패는 **같은 토큰·줄·문단의 무한 반복**이고, 최대 컨텍스트
  초과 또는 JSON 스키마 검증 실패로 잡아낸다. 우리 `_ask()` 는 JSON 파싱 실패 시 예외를
  올린다(철학 5) — 같은 방어다. 다만 **길이 초과로 잘린 JSON** 은 파싱 실패로만 잡히고
  사유가 구분되지 않는다. `finish_reason == "length"` 를 따로 기록하면 더 낫다.

---

## 7. 출처

**논문**

| 주제 | 출처 | 검증 |
|---|---|---|
| 다단계 파이프라인 · 페이지 검색 ablation | [arXiv:2604.26462](https://arxiv.org/abs/2604.26462) | 2차 |
| 다단계 필드 추출 8.8배 | [arXiv:2510.23066](https://arxiv.org/abs/2510.23066) | 초록 원문 |
| olmOCR · document anchoring | [arXiv:2502.18443](https://arxiv.org/abs/2502.18443) | 본문 |
| ExtractConf 다신호 신뢰도 | [arXiv:2606.24420](https://arxiv.org/abs/2606.24420) | 2차 |
| VLM OCR 오류 회피 벤치마크 | [arXiv:2511.19806](https://arxiv.org/abs/2511.19806) | 2차 |
| VERDI · 구조화 출력 로그확률 포화 | [arXiv:2605.11334](https://arxiv.org/abs/2605.11334) | 초록 |
| Consensus Entropy 다중 VLM 합의 | [arXiv:2504.11101](https://arxiv.org/abs/2504.11101) | 본문 |
| 자기보고 확신도 보정 | [arXiv:2412.14737](https://arxiv.org/abs/2412.14737) · [arXiv:2509.25532](https://arxiv.org/abs/2509.25532) | 초록 |
| ExStrucTiny 스키마 가변 추출 | [arXiv:2602.12203](https://arxiv.org/abs/2602.12203) | 본문 |
| 채점 LLM 분리 | [arXiv:2510.19334](https://arxiv.org/abs/2510.19334) | 2차 |
| CONSTRUCT 필드별 신뢰도 | [arXiv:2603.18014](https://arxiv.org/abs/2603.18014) | 2차 |
| JSONSchemaBench 제약 디코딩 | [arXiv:2501.10868](https://arxiv.org/abs/2501.10868) | 2차 |
| CropVLM 확대 판독 | [arXiv:2511.19820](https://arxiv.org/abs/2511.19820) | 초록 |
| OmniDocBench | [arXiv:2412.07626](https://arxiv.org/abs/2412.07626) | 초록 |
| 손글씨 HTR | [arXiv:2503.15195](https://arxiv.org/abs/2503.15195) | 2차 |
| 양식·체크박스 | [arXiv:2604.16504](https://arxiv.org/abs/2604.16504) | 2차 |

**공식 문서**

- [Claude Citations](https://platform.claude.com/docs/en/build-with-claude/citations) — 원문 확인
- [Anthropic Citations 발표](https://claude.com/blog/introducing-citations-api)
- [OpenAI Structured Outputs](https://openai.com/index/introducing-structured-outputs-in-the-api/)
- [OpenAI Batch API](https://developers.openai.com/api/docs/guides/batch)
- [Thinking Machines — Defeating Nondeterminism in LLM Inference](https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/)

**저장소**

- [opendatalab/OmniDocBench](https://github.com/opendatalab/OmniDocBench) — 문서 파싱 평가 방법론
- [arena-ai/structured-logprobs](https://github.com/arena-ai/structured-logprobs) — 구조화 출력의 필드별 로그확률 추출
- [cleanlab/structured-output-benchmark](https://github.com/cleanlab/structured-output-benchmark) — CONSTRUCT 논문 코드
- [WalidHadri-Iron/awesome-ocr-2026](https://github.com/WalidHadri-Iron/awesome-ocr-2026) — 배포 패턴·선택 가이드
- [k-arvanitis/awesome-document-ocr](https://github.com/k-arvanitis/awesome-document-ocr)
- [Yuliang-Liu/AWESOME-OCR-LLM](https://github.com/yuliang-liu/awesome-ocr-llm)

**분석 글**

- [LlamaIndex — OmniDocBench is Saturated](https://www.llamaindex.ai/blog/omnidocbench-is-saturated-what-s-next-for-ocr-benchmarks)

---

## 8. 추가 조사가 필요한 영역

1. **`검증: 2차` 표시된 수치의 원문 재확인** — 특히 ExtractConf 표(AUC 0.928, 커버리지
   80%→99.1%)와 abstention 벤치마크 표. 의사결정의 근거가 되므로 발표 전 원문 확인 필요.
2. **골든셋 30건으로 신뢰도 보정이 되는가.** 관측이 840개라도 필드 유형이 편중되면 보정
   곡선이 흔들린다. 홀드아웃과의 배분 설계가 필요하다.
3. **텍스트 앵커링의 실제 이득.** olmOCR 은 "환각이 뚜렷이 줄었다"고만 적고 **정량 ablation
   표를 제시하지 않는다.** 우리 골든셋으로 직접 재야 한다.
4. **OpenAI Batch API + prompt caching 병용 조건.** 1,021건 배치 처리 비용과 직결되는데
   커뮤니티 정보가 엇갈린다. 공식 문서로 확인 필요.
5. **1비트 150DPI 팩스 스캔에 대한 공개 근거 부재.** 우리 코퍼스의 지배적 형태인데 어느
   벤치마크에도 없다. 이 자체가 발표에서 말할 값어치가 있는 관찰이다.
