# -*- coding: utf-8 -*-
"""
readme/output_sample.xlsx → schema/fields.yaml 생성

  python tools/gen_schema.py

엑셀이 단일 소스이므로 전사 오류가 없고, 표준 전문가가 엑셀을 고치면 재생성하면 된다.
엑셀에 아직 없는 메타(출처·안전등급·MVP 여부)는 아래 표에서 주입한다.
"""
import os, re, sys, io
sys.stdout.reconfigure(encoding="utf-8")
import openpyxl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC  = os.path.join(ROOT, "readme", "output_sample.xlsx")
OUT  = os.path.join(ROOT, "schema", "fields.yaml")

# ── 엑셀에 아직 없는 메타 (확정된 결정사항) ──────────────────────────
# 안전등급: safety(안전) / identity(식별) / normal(일반)
SAFETY = {
    "ENGINEERING TAG NO.":   "identity",   # 오적재 시 타 자산 덮어씀
    "ACTUATOR FAIL ACTION":  "safety",     # ATO/ATC 역전, 비상시 거동
}
# MVP 수직 슬라이스 대상 (각각 다른 코드 경로를 대표)
MVP = [
    "ENGINEERING TAG NO.",      # 식별
    "MANUFACTURER",             # 라벨 인접값
    "MODEL NO.",                # 라벨 인접값
    "VALVE BODY SIZE",          # 표기 다양성
    "VALVE BODY RATING",        # 표기 다양성
    "VALVE BODY MATERIAL",      # 자유 텍스트 편차
    "ACTUATOR FAIL ACTION",     # 도메인 규칙 역전
    "RATED CV NORMAL",          # 신규 컬럼 → N/A 경로 검증
]
# 공정 조건은 필수에서 제외 (2026-08-21 결정) — 데이터시트 기재율이 낮음
OPTIONAL_GROUPS = {"공정 조건"}

# 확신도 임계값 기본값 — 개발 30건으로 보정 예정
THRESHOLD = {"safety": 1.00, "identity": 1.00, "normal": 0.90}


def key_of(name: str) -> str:
    k = name.lower()
    k = re.sub(r"\(.*?\)", " ", k)
    k = re.sub(r"[^a-z0-9]+", "_", k).strip("_")
    return k


def q(s):
    """YAML 안전 인용"""
    if s is None:
        return '""'
    s = str(s).strip().replace('"', '\\"')
    return f'"{s}"'


def main():
    wb = openpyxl.load_workbook(SRC, data_only=True)
    ws = wb["Output"]

    # A열 라벨 → 행 번호
    rows = {}
    for r in range(1, ws.max_row + 1):
        lab = ws.cell(r, 1).value
        if lab:
            rows.setdefault(str(lab).strip(), r)

    R_CODE = rows.get("DB CODE")
    R_GRP  = rows.get("대분류")
    R_NAME = rows.get("분류")
    R_DESC = rows.get("DESCRIPTION")
    R_EX   = rows.get("EXAMPLE")
    R_REQ  = rows.get("필수여부")
    R_ALI  = rows.get("유사 표현")
    if not all([R_CODE, R_GRP, R_NAME, R_DESC, R_REQ]):
        sys.exit(f"[중단] 필수 행을 찾지 못함: {rows}")

    fields, seen = [], set()
    for c in range(2, ws.max_column + 1):
        name = ws.cell(R_NAME, c).value
        if not name:
            continue
        name = str(name).strip()
        grp  = str(ws.cell(R_GRP, c).value or "").strip()
        code = str(ws.cell(R_CODE, c).value or "").strip()

        # 유사 표현: 유사표현 행부터 연속된 행을 모두 수집
        aliases = []
        if R_ALI:
            r = R_ALI
            while r <= ws.max_row:
                lab = ws.cell(r, 1).value
                if r != R_ALI and lab:      # 다음 라벨 행 만나면 종료
                    break
                v = ws.cell(r, c).value
                if v and str(v).strip():
                    aliases.append(str(v).strip())
                r += 1

        req = str(ws.cell(R_REQ, c).value or "").strip().upper() == "O"
        if grp in OPTIONAL_GROUPS:
            req = False                      # 공정 조건 필수 제외 결정 반영

        k = key_of(name)
        if k in seen:
            k = f"{k}_{c}"
        seen.add(k)

        safety = SAFETY.get(name, "normal")
        fields.append(dict(
            key=k, name=name, group=grp, db_code=code,
            desc=str(ws.cell(R_DESC, c).value or "").strip(),
            example=str(ws.cell(R_EX, c).value or "").strip() if R_EX else "",
            required=req, safety=safety, aliases=aliases,
            mvp=(name in MVP), threshold=THRESHOLD[safety],
        ))

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    L = []
    A = L.append
    A("# 컨트롤밸브 기준정보 필드 정의서")
    A("#")
    A("# 이 파일은 readme/output_sample.xlsx 에서 자동 생성됨.")
    A("#   재생성: python tools/gen_schema.py")
    A("# 직접 편집하지 말고 엑셀을 수정한 뒤 재생성할 것.")
    A("#")
    A("# safety : safety(안전) / identity(식별) / normal(일반)")
    A("#          safety·identity 는 확신도와 무관하게 사람 확인이 필요한 필드")
    A("# source : document(문서 추출) / derived(규칙 파생) / system(시스템)")
    A("#          MVP 에서는 전부 document — 스마트 계장 파생 필드는 범위 외")
    A("# mvp    : MVP 수직 슬라이스 대상 여부")
    A("")
    A("meta:")
    A(f"  equipment: {q('Control Valve')}")
    A(f"  field_count: {len(fields)}")
    A(f"  mvp_count: {sum(1 for f in fields if f['mvp'])}")
    A(f"  required_count: {sum(1 for f in fields if f['required'])}")
    A(f"  source_workbook: {q('readme/output_sample.xlsx')}")
    A("")
    A("fields:")
    for f in fields:
        A(f"  - key: {f['key']}")
        A(f"    name: {q(f['name'])}")
        A(f"    group: {q(f['group'])}")
        A(f"    db_code: {q(f['db_code'])}")
        A(f"    desc: {q(f['desc'])}")
        if f["example"]:
            A(f"    example: {q(f['example'])}")
        A(f"    required: {str(f['required']).lower()}")
        A(f"    safety: {f['safety']}")
        A(f"    source: document")
        A(f"    mvp: {str(f['mvp']).lower()}")
        A(f"    threshold: {f['threshold']:.2f}")
        if f["aliases"]:
            A("    aliases:")
            for a in f["aliases"]:
                A(f"      - {q(a)}")
        else:
            A("    aliases: []          # 라벨링 중 raw_label 로 수집 예정")
        A("")

    io.open(OUT, "w", encoding="utf-8", newline="\n").write("\n".join(L))

    print(f"생성: {OUT}")
    print(f"  필드 {len(fields)}개 / 필수 {sum(1 for f in fields if f['required'])}개 "
          f"/ MVP {sum(1 for f in fields if f['mvp'])}개")
    print(f"  안전등급: " + ", ".join(
        f"{s}={sum(1 for f in fields if f['safety']==s)}" for s in ("safety","identity","normal")))
    have = sum(1 for f in fields if f["aliases"])
    print(f"  유사표현 보유: {have}/{len(fields)}개 필드  (나머지는 라벨링으로 수집)")
    missing = [f["name"] for f in fields if f["mvp"] and not f["aliases"]]
    if missing:
        print(f"  ※ MVP 필드 중 유사표현 미기재: {len(missing)}개")


if __name__ == "__main__":
    main()
