# 화면 작업 인수인계 — 새 세션에 그대로 붙여넣을 것

작성 2026-08-25 · 발표 2026-08-27

**아래 「붙여넣을 프롬프트」를 새 세션 첫 메시지로 넣으면 된다.**
설계 배경은 `ui_spec.md`, 결정 근거는 `ARCHITECTURE.md`, 측정은 `eval_history.md`.

---

## 붙여넣을 프롬프트

```
D2S PoC 의 화면(Streamlit)을 고치려고 한다. 발표가 2026-08-27 이다.

먼저 읽을 것 — docs/ui_spec.md · docs/ui_handoff.md · src/ui/CLAUDE.md
그리고 CLAUDE.md 의 개발 철학 6개.

## 지금 구조 (이미 동작한다)

app.py                라우팅만. Streamlit 진입점
src/ui/session.py     단계 기계 + session_state.
                      MAIN → UPLOAD → CONFIRM → EXTRACT → HITL → DONE
                      session.page() · use_vlm() · doc() · go() 가 이미 있다
src/ui/screens.py     main / upload / confirm / extract / done
src/ui/hitl.py        검증 화면 (핵심, 310줄). 좌 지면+bbox / 우 항목표
src/ui/source.py      UiDoc 공급. from_vlm(path, only_mvp, page) ·
                      render_page_png(path, page)
src/ui/overlay.py     bbox 하이라이트
src/ui/export.py      마스터 엑셀 (열 순서는 계약이다)
src/ui/theme.py       상태 → 한글 라벨

## 해야 할 것 세 가지

### ① CONFIRM 화면을 「쪽 고르기」로 확장   ← 최우선

지금은 파일명만 보여주고 "N페이지를 판독합니다" 라고 쓴 뒤 시작 버튼이다.
페이지 번호가 어디서 오는지 사용자가 정할 수 없다.

바꿀 것 — 모든 쪽을 축소 이미지로 띄우고 사람이 사양표를 고른다.
고른 값을 session.page() 에 넣으면 나머지는 이미 연결돼 있다.

쓸 도구(이미 있다):
    src.preprocess.render_pages(path, out_dir, pages=None)
    src.preprocess.make_montage(imgs, out_dir)      # 격자 이미지
    src.preprocess.probe_pages(path)                # 쪽 수 · 텍스트레이어

⚠️ 반드시 지켜야 할 것 — 사양표가 두 장인 문서가 있다.
   10FV011 은 2003년 개조본과 1986년 원본이 한 파일에 있고,
   원본에는 손으로 OLD 라고 적혀 있다. 축소 이미지에서는 그 글씨가 안 보인다.
   그리고 이건 숙련자도 틀린 케이스다.
   → 사양표 후보가 2장 이상이면 축소가 아니라 **날짜·개정표기를 뽑아
     나란히 크게** 보여준다. src.preprocess.parse_doc_date · find_marks 가 한다.

덤 — 사람이 고를 때마다 "규칙으로 골랐으면 같은 답이었나" 를 조용히 기록하면
자동 선택 정확도가 공짜로 측정된다. hooks.on_human_action 으로 흘리면 된다.

### ② HITL 화면에 「왜 확인필요인지」 표시

지금은 상태(자동확정/확인필요/근거없음)만 보인다. 어느 수단이 이 칸을
불렀는지가 보여야 사람이 무엇을 확인할지 안다. 표시원이 셋이고 전부 코드다:

    확신도 미달   f.threshold 와 rec.confidence 비교
    허용 어휘 밖   src.validate.domain.vocabulary.check(field, value)
    출처 의심     src.validate.domain.provenance.check(field, value, ex, ctx)

그리고 원문과 표준값을 함께 보인다 — 안 바뀐 것은 하나만,
바뀐 것은 `EQ% → EQUAL PERCENTAGE` 처럼 둘 다. transform_trace 에 있다.

### ③ 「사전 승인」 화면 (신규)

새 표기가 관측되면 사람이 승인해야 규칙이 된다. 이게 Loop C 의 입구이고,
이 과제를 도구가 아니라 시스템으로 만드는 부분이다.

데이터는 이미 나온다:
    src.validate.domain.vocabulary.as_rows()
    → [{field_key, value, count, docs, labels, nearest, correctable, note}]

지켜야 할 것 넷:
  1. 문서마다 묻지 말고 **실행이 끝난 뒤 한 번에** 보여준다.
     매번 물으면 사람이 읽지 않고 승인하게 되고 안전장치가 형식만 남는다.
  2. **빈도순** 정렬. 40건에서 나온 표현과 1건짜리는 무게가 다르다.
  3. **기본값은 "무시"**. 승인이 의도적 행위여야 한다.
  4. `nearest`(가장 가까운 허용값)는 **보여주기만** 한다. 자동으로 고치지 않는다
     — C5(Cr-Mo 합금강)와 CS(탄소강)는 한 글자 차이지만 다른 재질이다.

승인된 것은 schema/rules.yaml 로 간다. 기계가 자동으로 쓰지 않는다.

## 손대면 안 되는 것

src/contracts.py · schema/*.yaml · src/pipeline.py · src/hooks.py ·
src/preprocess.py — 소유자만 수정한다. 필요하면 요청한다.
src/ui/ 안에서만 작업한다.

## 자기검증

src/ui/test_flow.py 가 화면 흐름과 잠금을 파서 없이 검증한다.
바꾸면 여기도 같이 고친다. `python -m pytest src/ui -q`
```

---

## 화면 세션에 넘기지 않는 것 (참고)

측정·규칙 쪽은 다른 세션이 계속한다. 화면 세션은 아래를 건드리지 않는다.

- `eval/` 전체 — 평가 하네스·원문 보관·비교 도구
- `schema/rules.yaml` — 표기 사전·허용 어휘
- `src/parsers/vlm/` — 판독 프롬프트
- `src/validate/domain/` — 어휘·출처 검증 (**읽어서 쓰기만** 한다)

## 화면이 의존하는 최신 결정

| 결정 | 날짜 | 화면에 미치는 영향 |
|---|---|---|
| 쪽 선택은 **사람이** 한다 | 08-25 | 자동 선별 에이전트를 만들지 않는다. 화면이 파이프라인의 일부다 |
| 표기 사전은 **코드**다 | 08-25 | 승인 화면은 규칙 파일을 고치는 것이지 모델을 부르는 게 아니다 |
| 안전 필드는 **항상 사람 확인** | — | `actuator_fail_action` · `engineering_tag_no` |
| 상급 모델 2차 판독은 **기본 꺼짐** | 08-24 | 화면에 "2차 판독" 버튼을 만들지 않는다 |
| 어휘 밖 값은 **바꾸지 않고 표시만** | 08-25 | 화면이 값을 자동 교정하지 않는다 |

## 실측에서 나온 화면 관련 사실

- **안전 필드가 확신도 0.98로 뒤집힌 사례가 있다** — `11LV008` 에서 체크박스
  `Open ☒` 를 `Close` 로 읽었다. 확신도·어휘·출처 어느 수단도 못 잡는다.
  **사람이 지면을 보고 확인하는 것 외에 방법이 없다** — bbox 하이라이트가
  이 화면의 존재 이유다.
- bbox 가 실물에서 **한 행 어긋나는 경우**가 있다. 알려진 한계다.
- 21건 기준 정확도 84%, 그중 **틀린 값의 43%만 표시가 붙는다.**
  나머지는 사람이 지면을 봐야 걸린다.
