# -*- coding: utf-8 -*-
"""환경 자체 진단 — 무엇이 문제이고 무엇을 해야 하는지 직접 알려준다."""
import importlib, os, sys
sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ok = True

def chk(label, cond, fix=""):
    global ok
    if cond:
        print(f"  OK    {label}")
    else:
        ok = False
        print(f"  실패  {label}")
        if fix:
            print(f"        → {fix}")

print("=" * 52)
print("  D2S PoC - 환경 진단")
print("=" * 52)

print("\n[파이썬]")
chk(f"버전 {sys.version_info.major}.{sys.version_info.minor}",
    sys.version_info >= (3, 10), "Python 3.10 이상이 필요합니다")

print("\n[패키지]")
for m in ("openpyxl", "xlrd", "fitz", "yaml", "PIL", "pandas",
          "streamlit", "anthropic", "dotenv", "pytest"):
    try:
        importlib.import_module(m)
        chk(m, True)
    except ImportError:
        chk(m, False, "setup.bat 을 다시 실행하세요")

print("\n[프로젝트 파일]")
for p in ("src/contracts.py", "schema/fields.yaml", "schema/rules.yaml", "CLAUDE.md"):
    chk(p, os.path.exists(os.path.join(ROOT, p)), "Fetch/Pull 로 최신 코드를 받으세요")

print("\n[스키마]")
try:
    import yaml
    with open(os.path.join(ROOT, "schema/fields.yaml"), encoding="utf-8") as f:
        d = yaml.safe_load(f)
    n = len(d.get("fields", []))
    chk(f"필드 {n}개 로드", n == 30, "gen_schema.py 로 재생성이 필요할 수 있습니다")
except Exception as e:
    chk("fields.yaml 파싱", False, str(e))

print("\n[API 키]")
env = os.path.join(ROOT, ".env")
if os.path.exists(env):
    txt = open(env, encoding="utf-8", errors="ignore").read()
    chk(".env 에 키 입력됨", "여기에" not in txt and "ANTHROPIC_API_KEY=" in txt and
        len(txt.split("ANTHROPIC_API_KEY=")[-1].strip()) > 10,
        ".env 를 메모장으로 열어 API 키를 넣어주세요")
else:
    chk(".env 존재", False, "setup.bat 을 실행하세요")

print("\n[문서 원본]")
rf = os.path.join(ROOT, "raw_file")
chk("raw_file 폴더", os.path.isdir(rf),
    "문서 원본을 raw_file 폴더에 넣어주세요 (Git 에는 올리지 않습니다)")

print("\n" + "=" * 52)
print("  준비 완료" if ok else "  위의 [실패] 항목을 해결한 뒤 다시 실행하세요")
print("=" * 52)
