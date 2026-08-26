# 제안 — 파생 필드 도출 (`type_name`)

작성 2026-08-26 · 브랜치 `proposal/derived-fields` · 제안자 강민호 책임 (③-b VLM Parser)

**이 문서는 제안이다.** 아래 변경 중 다섯 곳이 **다른 담당자 소유 파일**이라, 병합 전에
소유자 승인이 필요하다. §5 에 충돌 사항을 전부 적었다.

---

## 요약

- **문제** — 골든셋 빈 필드 121건 중 **18건이 문서에 글자로 없는 유추필드**였다. 골든셋
  라벨러도 원문라벨에 `NA (Tag에서 FV를 보고 유추)` 로 적었다. 파서가 `null` 을 낸 것은
  철학 4 를 지킨 정확한 동작인데, **④ Normalize 계약이 `run(ex, f)` 뿐이라 다른 필드를
  볼 수 없어** 그 자리를 채우지 못하고 전부 `NA` 로 확정됐다. `NO_EVIDENCE` 는 Loop A
  재시도 대상도 아니라 되돌릴 기회가 없었다.
- **수정** — ④에 `context`(앞서 확정된 필드 값들)를 넘기고, 필드 순서에 의존하지 않도록
  **파생 필드 2차 패스**를 두었다. **`type_name` 만 도출한다** — 태그에서 설비종류
  (`FV`·`LV`·`PV` …)를 규칙으로 뽑아 매핑에서 찾는다. 규칙은 코드가 아니라
  `schema/rules.yaml` 에 둔다(철학 2). 도출값은 **확신도 0 으로 고정**해 자동확정을 막았다.
- **검증** — 골든셋 30문서 **613칸 A/B**(VLM 호출 0회): 성공률 **83.2% → 84.5%**,
  `type_name` 을 포함한 대상 필드 **49.0% → 64.7%**, **나머지 562칸은 전 칸 동일
  (회귀 0건)**. 판정이 바뀐 8칸은 전부 개선·전부 `review`·값 **8/8 정답 일치**.
  `pytest 352 passed · 2 skipped`, `--smoke` 전 구간 정상.
- **변경점** — `schema/rules.yaml`(서경빈 선임) · `src/schema.py` · `src/normalize/` ·
  `src/pipeline.py`(이종수 책임) 5개 파일. **`src/parsers/vlm/` 와 `src/contracts.py` 는
  변경 없음** — 파서는 계속 "문서에 없으면 null" 을 지키고 채우는 일은 하류 규칙이 맡는다.
  소유자 승인 전이라 병합하지 않고 브랜치 제안으로 둔다.

---

## 1. 변경 배경

### 1.1 발견 — `no_evidence` 로 확정되어 되돌릴 수 없는 칸이 있다

골든셋을 VLM 경로로 돌려 빈 필드 121건의 사유를 조사했다(API 0회, 캐시 재사용).

| `absence_reason` | 건수 | 비율 |
|---|---|---|
| `no_evidence` | 115 | 95.0% |
| `checkbox_ambiguous` | 5 | 4.1% |
| `unreadable` | 1 | 0.8% |

`no_evidence` 는 `FailureKind.NO_EVIDENCE` → `FieldState.NA` 로 **확정**되고
**Loop A 재시도 대상도 아니다**(`pipeline.py` 의 재시도는 `EXTRACTION` 만 본다).
즉 여기서 비면 사람이 채우는 것 말고 방법이 없다.

### 1.2 그런데 그중 상당수는 파서 잘못이 아니었다

골든셋의 「원문라벨」 열과 대조하니 원인이 갈렸다.

| 최종 원인 | 건수 |
|---|---|
| 정상 — 골든셋도 N/A | 94 |
| **① 유추필드 — 문서에 글자가 없음** | **18** |
| ④ 스캔이라 확인 불가 | 6 |
| ② 판독 실패 — 텍스트층에 정답이 있는데 못 읽음 | 3 |

①이 이 제안의 대상이다. 라벨러가 원문라벨 칸에 이렇게 적어 두었다.

```
TYPE NAME    정답값 "Flow Control Valve"
             원문라벨 "NA (Tag에서 FV를 보고 Flow Control Valve로 유추)"

FLUID STATE  정답값 "LIQUID"
             원문라벨 "NA (FLUID NAME을 보고 유추)"
```

**사람도 문서에서 읽은 것이 아니라 다른 필드에서 도출했다.** 그러니 파서가
`null` 을 낸 것은 정확한 동작이다 — CLAUDE.md 철학 4(근거 없는 값을 만들지
않는다)와 `SYSTEM` 프롬프트 2항(문서에 없으면 null, 절대 만들어내지 않는다)을
그대로 지킨 결과다.

### 1.3 그런데 ④ Normalize 가 그 자리를 채울 수 없다

골든셋 30건에 파이프라인을 돌려 두 필드를 추적했다.

| | `type_name` | `fluid_state` |
|---|---|---|
| ③ VLM 이 값을 냄 | 17 / 30 | 13 / 30 |
| **④ Normalize 후 값 있음** | **17** | **13** |

**숫자가 같다 — ④ 가 새로 만들어 낸 값이 0건이다.** 원인은 계약이다.

```python
class NormalizeModule(Protocol):
    def run(self, ex: RawExtraction, f: Field) -> tuple[str | None, list[str]]:
                   ^^^^^^^^^^^^^^^^  ^^^^^^^^
                   추출 1건           필드 1개     ← 다른 필드를 볼 수 없다
```

대조적으로 **⑤ Validate 는 `context` 를 받는다**(`check(f, value, ex, context)`).
같은 `_process()` 안에서 ④만 못 받는 **비대칭**이 이 문제의 직접 원인이다.

그리고 `src/normalize/` 는 `CLAUDE.md` 와 빈 `__init__.py` 뿐 — 미구현이라
`pipeline.DefaultNormalize` 가 돌고 있었다.

---

## 2. 변경 내용

### 2.1 파일별 변경

| # | 파일 | 변경 | 소유 |
|---|---|---|---|
| 1 | `schema/rules.yaml` | `derived_fields` 블록 신설 (+41줄) | **서경빈 선임** |
| 2 | `src/schema.py` | `derived_fields()` · `derivation_for()` 로더 (+16줄) | **이종수 책임** |
| 3 | `src/normalize/__init__.py` | `Normalizer` 구현 (신규, 미구현 자리) | **이종수 책임** |
| 4 | `src/normalize/test_normalize.py` | 자기 검증 12건 (신규) | **이종수 책임** |
| 5 | `src/pipeline.py` | 계약 확장 · 호출부 · **2차 패스** · 확신도 안전장치 · `build()` 주입 | **이종수 책임** |
| — | `src/parsers/vlm/` | **변경 없음** | 강민호 책임 |
| — | `src/contracts.py` | **변경 없음** | 이종수 책임 |

**③-b 파서는 한 줄도 바뀌지 않는다.** 파서는 계속 "문서에 없으면 null" 을 지키고,
채우는 일은 하류 규칙이 맡는다.

### 2.2 규칙 (`schema/rules.yaml`)

```yaml
derived_fields:
  enabled: true
  type_name:
    from: engineering_tag_no
    how: tag_kind                  # 태그의 설비종류 두세 글자로 판정
    map: { FV: "Flow Control Valve", LV: "Level Control Valve",
           PV: "Pressure Control Valve", TV: "Temperature Control Valve",
           HV: "Hand Valve", XV: "On-Off Valve",
           PCV: "Pressure Control Valve", PDV: "Pressure Differential Valve",
           FCV: "Flow Control Valve", LCV: "Level Control Valve",
           TCV: "Temperature Control Valve" }
  # fluid_state 는 도출하지 않는다 (2026-08-26 협의) — §2.5 참조
```

규칙을 코드가 아니라 yaml 에 둔 것은 철학 2 다. `model_to_manufacturer` 가
이미 같은 형식의 선례다 — **문서에 없는 값을 규칙으로 채우고 근거를 남긴다.**

### 2.3 2차 패스 (`src/pipeline.py`)

`fields.yaml` 첫 필드가 `type_name` 이라, 1차 처리 시점에는 `context` 가 비어 있어
태그를 볼 수 없다. 그래서 1차가 끝난 뒤 한 번 더 돈다.

```python
# 1차 처리 (기존 그대로)
for f in fields:
    rec = self._process(..., context, attempt=0)
    records[f.key] = rec
    context[f.key] = rec.value

# ★ 파생 필드 2차 패스
for f in fields:
    if records[f.key].failure is not FailureKind.NO_EVIDENCE:
        continue                                   # 값이 있거나 다른 실패면 대상 아님
    if schema.derivation_for(f.key) is None:
        continue                                   # 도출 규칙이 없으면 그대로 둔다
    rec = self._process(..., context, attempt=0)   # context 가 다 찬 상태
    if rec.value:
        records[f.key] = rec
        context[f.key] = rec.value
```

**필드 순서에 의존하지 않는다.** `fields.yaml` 순서가 바뀌어도 깨지지 않는다.

### 2.4 확신도 안전장치 — 실측으로 필요성이 드러났다

첫 구현에서 **도출값 11건이 `auto` 로 자동확정됐다.** 원인은 이랬다.

> 파서가 `null` 을 내면서 `confidence` 는 높게 준다. "값이 없다는 데 대한 확신"
> 이지 "도출값이 맞다는 확신" 이 아닌데, 그 값이 레코드에 남아 임계(0.90)를
> 통과했다.

도출 지점(1차·2차 패스)을 가리지 않고 한 곳에서 막았다.

```python
# _process() 안, normalize 직후
if value and not ex.found:
    ex = replace(ex, confidence=0.0)
```

**파서가 값을 못 냈는데 ④가 값을 만들었으면 그 값은 문서 근거가 없다.**
확신도 0 → `_decide()` 의 임계에 걸려 `REVIEW` 로 간다. `DualParser._merge()` 가
텍스트 단독 값을 다루는 방식과 같다.

조치 후 **자동확정 0건 · 전량 `REVIEW`** 로 바뀌었다.

### 2.5 `fluid_state` 는 도출 대상에서 뺐다 (2026-08-26 협의)

초안에는 `fluid_state` 도 들어 있었다. 유체명에 기체 낱말(`STEAM`·`GAS`)이 있으면
`GAS`, 없으면 `LIQUID` 로 두는 규칙이었고 골든셋 30건에서 전부 맞았다
(GAS 7건이 모두 낱말을 포함했고 LIQUID 18건은 하나도 포함하지 않았다).

**그런데 유체명만으로 상태를 확정할 수 없는 경우가 있다.** 같은 유체가 공정
조건에 따라 액체이기도 기체이기도 하고, 이상(two-phase) 흐름도 있다. 골든셋에서
전부 맞은 것은 표본이 좁아서지 규칙이 옳아서가 아니다.

**③-b 가 문서에서 못 읽으면 채우지 않고 `NA` 로 둔다.** 사람이 판단한다.
그래서 개선폭이 18칸 → **8칸**으로 줄었지만, 근거 없는 값을 만들지 않는 쪽을
택했다(철학 4).

---

## 3. 테스트 결과

### 3.1 골든셋 전수 A/B

`schema/rules.yaml` 의 `derived_fields.enabled` 만 토글해 같은 채점기
(`src/parsers/text/score_against_kit.py` 의 판정 로직)로 두 번 돌렸다.
**VLM 호출 0회**(캐시 재사용), 문서 30건 · 채점 613칸.

| | 칸 | 정확 | 표기차이 | 정규화대기 | 오답 | 미추출 | 성공률 |
|---|---|---|---|---|---|---|---|
| **OFF** (현행) | 613 | 357 | 99 | 54 | 51 | 52 | **83.2%** |
| **ON** (제안) | 613 | **365** | 99 | 54 | **51** | **44** | **84.5%** |

### 3.2 대상 두 필드

| | 칸 | 정확 | 오답 | 미추출 | 성공률 |
|---|---|---|---|---|---|
| OFF | 51 | 25 | 3 | 23 | **49.0%** |
| ON | 51 | **33** | 3 | **15** | **64.7%** |

### 3.3 회귀 확인 — 그 밖의 562칸

| | 칸 | 정확 | 표기차이 | 정규화대기 | 오답 | 미추출 | 성공률 |
|---|---|---|---|---|---|---|---|
| OFF | 562 | 332 | 99 | 54 | 48 | 29 | 86.3% |
| ON | 562 | 332 | 99 | 54 | 48 | 29 | 86.3% |

**모든 칸이 동일하다. 다른 판정이 fail 로 바뀐 케이스는 0건이다.**

### 3.4 판정이 바뀐 8칸 — 전부 개선

```
방향 분포   {'개선': 8}
ON 상태     {'review': 8}      ← 자동확정 0
값-정답     8 / 8 일치
```

| 문서 | 필드 | OFF → ON | 도출 근거 (태그) |
|---|---|---|---|
| d005 | type_name | 미추출 → 정확 | `10-FV-079` → FV |
| d006 | type_name | 미추출 → 정확 | `10-PV-018` → PV |
| d007 | type_name | 미추출 → 정확 | `15-LV-015` → LV |
| d010 | type_name | 미추출 → 정확 | `52-PV-014` → PV |
| d026 | type_name | 미추출 → 정확 | `70-LV-012` → LV |
| d028 | type_name | 미추출 → 정확 | `10-PDV-067` → PDV |
| d029 | type_name | 미추출 → 정확 | `10-PV-018` → PV |
| d031 | type_name | 미추출 → 정확 | `10-PV-081` → PV |

### 3.5 기존 테스트

```
pytest                    352 passed · 2 skipped   (48초)
python -m src.pipeline --smoke   전 구간 정상
src/normalize/ 신규 테스트        12 passed
```

### 3.6 실측이 드러낸 한계 — 규칙은 확정적이지 않다

골든셋에 **태그만으로는 결정되지 않는 실물이 둘** 있다.

```
11-LV-001    LV 인데 골든셋 정답이 Flow Control Valve
22-PCV-013   PCV 인데 정답이 Direct Operated Regulator (레귤레이터 계열)
```

`fluid_state` 의 낱말 규칙도 같은 이유로 뺐다(§2.5). 태그 매핑 역시
**골든셋 31건 관찰에서 뽑은 것**이라 코퍼스 전체를 대변하지 않는다 —
측정된 이득(+1.3%p)에는 이 과적합이 섞여 있다.

**그래서 도출값을 `REVIEW` 로 보내는 것이 설계의 핵심이다.** 규칙이 틀려도
사람이 걸러낸다. 자동확정으로 보내면 이 두 예외가 조용히 오답이 된다.

---

## 4. 성능·비용

| | |
|---|---|
| VLM 호출 증가 | **0회** — ④는 규칙 계산만 한다 |
| 처리 시간 | 30문서 16초 → 22초 (2차 패스가 도출 대상 필드만 재처리) |
| 사람 작업량 | `NA` 18칸(빈칸 직접 입력) → `REVIEW` 18칸(후보 확인) |

**자동확정률은 오르지 않는다.** 사람이 채우던 빈칸이 확인할 후보로 바뀌는 것이다.

---

## 5. 규약과 충돌 사항

### 5.1 `src/contracts.py`

| 항목 | 내용 | 판정 |
|---|---|---|
| `RawExtraction` · `FieldRecord` · `TriageResult` | **변경 없음** | 충돌 없음 |
| `FailureKind` | 새 값을 만들지 않았다. `NO_EVIDENCE` 를 2차 패스의 조건으로 읽기만 한다 | 충돌 없음 |
| `FieldState` | 새 상태를 만들지 않았다. 도출값은 기존 `REVIEW` 로 간다 | 충돌 없음 |
| `FieldRecord.transform_trace` | 주석의 예시 형식(`"규칙 ATO → ..."`)을 그대로 따랐다 | 부합 |

**계약 3개(`TriageResult` → `RawExtraction` → `FieldRecord`)는 그대로다.**
바뀐 것은 `pipeline.py` 의 **모듈 프로토콜**(`NormalizeModule`)이고, 이는
`contracts.py` 가 아니라 하네스 쪽이다.

### 5.2 `NormalizeModule` 프로토콜 확장 — 충돌 있음

```python
# 이전
def run(self, ex: RawExtraction, f: Field) -> tuple[str | None, list[str]]

# 제안
def run(self, ex, f, context: dict[str, str | None] | None = None) -> ...
```

· **완화책** — 호출부가 `TypeError` 를 잡아 2인자로 다시 부른다. `reread` 에
  `attempt` 를 더할 때 쓴 방식과 같다(`pipeline.py` 의 기존 관용구).
· **영향받는 구현체** — `DefaultNormalize` 하나뿐이고, 3인자를 안 받아도 폴백으로 동작한다.
· **승인 필요** — 이종수 책임.

### 5.3 `docs/roles/이종수 책임.md`

> **소유 — 나만 수정하는 파일**
> `src/contracts.py` · `src/pipeline.py` · `src/hooks.py` · `src/preprocess.py` …
> **계약 변경 승인** — 다른 사람이 요청하면 여기서 결정한다.

**이 제안은 `src/pipeline.py` 와 `src/schema.py` 를 고치고 `src/normalize/` 를
구현했다. 셋 다 이종수 책임 소유다.** 브랜치로 만든 이유가 이것이며,
**병합 전 승인이 필요하다.**

### 5.4 `src/normalize/CLAUDE.md` — 문서와 계약이 이미 어긋나 있었다

모듈 지시서는 입력을 이렇게 적었다.

> `- 입력: RawExtraction[]`   ← **복수**

그런데 계약은 단수였다(`run(ex, f)`). **모듈 문서대로면 배열을 받으니 필드 간
도출이 원래 가능했어야 한다.** 이 제안은 문서 쪽에 맞추는 방향이다.

지시서의 「할 일」과도 부합한다.

> `3. schema/rules.yaml 을 읽어 적용 (코드에 규칙을 넣지 않는다)`

「하지 말 것」과의 관계도 확인했다.

> `- 범주형 허용값 정규화도 MVP 범위 외`

이 제안은 **허용값 정규화가 아니라 값 도출**이라 해당하지 않는다고 판단했다.
다만 경계가 모호하므로 소유자 확인이 필요하다.

### 5.5 루트 `CLAUDE.md`

| 조항 | 관계 | 판정 |
|---|---|---|
| **철학 1** 계약 밖을 만지지 않는다. `pipeline.py`·`schema/*.yaml` 은 소유자만 | **위반.** 그래서 브랜치 제안으로 만들고 승인을 요청한다 | **승인 필요** |
| **철학 2** 규칙은 코드가 아니라 `schema/*.yaml` 에 | 매핑·낱말목록을 전부 yaml 에 두었다 | 부합 |
| **철학 3** 모든 모듈은 fixture 로 자기 검증 | `src/normalize/test_normalize.py` 20건 | 부합 |
| **철학 4** 근거 없는 값을 만들지 않는다 | ⚠️ **가장 민감한 지점.** 아래 별도 서술 | 조건부 부합 |
| **철학 5** 실패를 삼키지 않는다 | 도출 실패 시 `trace` 에 사유를 남긴다(`도출 불가 — … 값이 없음`) | 부합 |
| **철학 6** 같은 입력 → 같은 출력 | 규칙 기반이라 결정론적. 난수·시각 미사용 | 부합 |

**철학 4 에 대하여** — 조항 전문은 이렇다.

> 모르면 `state=NA` + `note`. **추정값이 마스터DB 에 들어가면 이 과제가 해결하려는
> 문제를 재생산한다.**

이 제안은 문서에 없는 값을 만든다. 다음 셋으로 조항의 취지를 지켰다고 본다.

1. **파서는 계속 만들지 않는다.** ③은 `null` 을 내고, 도출은 하류 규칙이 한다
2. **자동확정되지 않는다.** 전량 `REVIEW` 로 사람에게 간다 (실측 18/18)
3. **근거가 남는다.** `transform_trace` 에 `engineering_tag_no='10-PDV-067' →
   type_name='Pressure Differential Valve' (문서 근거 없음 · 규칙 도출)` 로 기록되어
   되짚을 수 있다

그리고 **선례가 이미 있다.** `src/schema.py` 의 `model_to_manufacturer` 주석:

> 사실이지만 **문서에 없는 값을 채우는 것**이다. 반드시 `transform_trace` 에
> 근거를 남긴다 — 사람이 되짚을 수 있어야 한다(철학 4).

**같은 판단을 같은 방식으로 적용했다.**

### 5.6 `docs/roles/강민호 책임.md` (제안자 본인)

> 이 폴더 밖의 파일을 수정하지 않는다 (`preprocess.py` 변경은 이종수 책임에게 요청)

**이 제안은 그 규칙을 벗어난다.** 다만 `src/parsers/vlm/` 자체는 변경이 없고,
**형태를 "요청"이 아니라 "동작하는 제안 브랜치"로 만든 것**이다. 실측 없이
요청만 올리면 승인 판단에 필요한 숫자가 없기 때문이다.

### 5.7 `docs/ARCHITECTURE.md`

§9 소유권 표와 어긋나는 지점은 5.3 과 같다. §4(결정된 규칙)와 충돌하는 항목은
**찾지 못했다** — 파생 필드에 관한 결정이 아직 없다.

---

## 6. 승인이 필요한 것

| # | 요청 | 대상 |
|---|---|---|
| 1 | `NormalizeModule` 프로토콜에 `context` 추가 | 이종수 책임 |
| 2 | `_resolve_fields` 에 파생 필드 2차 패스 추가 | 이종수 책임 |
| 3 | `_process` 에 도출값 확신도 0 안전장치 추가 | 이종수 책임 |
| 4 | `src/normalize/` 구현 채택 | 이종수 책임 |
| 5 | `src/schema.py` 로더 2개 추가 | 이종수 책임 |
| 6 | `schema/rules.yaml` 의 `derived_fields` 규칙 검토 | 서경빈 선임 |

**6번이 특히 도메인 검토가 필요하다.** 태그 종류 매핑과 기체 낱말 목록은
골든셋 30건 관찰에서 뽑은 것이라 코퍼스 전체를 대변하지 않는다.

---

## 7. 재현 방법

```bash
git checkout proposal/derived-fields
python -m pytest src/normalize/ -q          # 도출 규칙 자기 검증 20건
python -m pytest -q                          # 전체 352 passed · 2 skipped
python -m src.pipeline --smoke               # 전 구간 정상
```

A/B 실행 스크립트는 세션 임시 폴더에 있다(`ab_test.py`). `derived_fields.enabled`
를 토글하며 두 번 돌려 같은 채점기로 비교한다. **캐시가 있으면 VLM 호출 0회**다.
