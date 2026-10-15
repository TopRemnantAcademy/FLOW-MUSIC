"""배포 의존성 감사 — `backend/requirements.txt` (Dockerfile 이 실제 설치하는 파일) 대비
`backend/app` 의 서드파티 import 를 AST 로 전수 대조한다.

분류 기준(출시 관점의 위험도):
  HARD   모듈 최상위 import + try/except ImportError 가드 없음
         → 패키지 누락 시 **컨테이너 기동 실패**(즉시 인지, 그러나 서비스 다운)
  SOFT   함수 내부 lazy import 이거나 try/except ImportError 로 감싸짐
         → 기동은 되지만 **기능이 조용히 비활성화**(인지 불가, 더 위험)
  LOCAL  퍼스트파티(backend/app 내부 상대 import) — 대상 아님

⚠ 이 저장소(FLOW-MUSIC)의 Dockerfile 은 `backend/Dockerfile` 이며 `backend/requirements.txt`
  를 설치한다. 저장소 루트의 `requirements-deploy.txt` 는 FLOW 코어에서 복사된 파일로
  이 프로젝트가 설치하지 않는다 — 그것을 기준으로 삼으면 python-jose/passlib 등이
  HARD 누락으로 오탐되어 진짜 경고를 덮는다(실측: #151).

사용:
    python scripts/check_deploy_deps.py
    python scripts/check_deploy_deps.py --requirements path/to/requirements.txt
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "backend" / "app"
# 이 저장소가 **실제로 설치하는** requirements (backend/Dockerfile:16 `-r requirements.txt`).
DEPLOY = ROOT / "backend" / "requirements.txt"
# 상대경로 인자 해석 기준(호출 위치와 무관하게 동작).
_SET_ROOT = ROOT

SKIP_PARTS = {"__pycache__", ".git", "node_modules", ".pytest_tmp", "venv", "venv313"}

# 절대 import 로 쓰이는 퍼스트파티 루트 (PyPI 패키지가 아님)
FIRST_PARTY_ROOTS = {"app", "backend", "admin", "user", "mobile", "tests", "scripts"}

# import 모듈명 → PyPI 배포명 (다르면 매핑)
ALIAS = {
    "docx": "python_docx",
    # python-jose 는 `jose` / `jwt` 로 import 된다. 매핑이 없으면 requirements 에
    # python-jose 가 있어도 `jose` 를 HARD 누락으로 오탐한다(#151 실측).
    "jose": "python_jose",
    "yaml": "pyyaml",
    "qdrant_client": "qdrant_client",
    "pydantic_settings": "pydantic_settings",
    "PIL": "pillow",
    "cv2": "opencv_python",
    "sklearn": "scikit_learn",
    "google": "google_genai",
    "google.generativeai": "google_generativeai",
}


def norm(name: str) -> str:
    return re.sub(r"[-.]", "_", name).lower()


def installed_names(path: Path) -> set[str]:
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        base = re.split(r"[<>=!~\[;]", line, 1)[0].strip()
        if base:
            out.add(norm(base))
    return out


def is_stdlib(mod: str) -> bool:
    return mod.split(".")[0] in sys.stdlib_module_names


def classify(tree: ast.Module) -> dict[str, dict]:
    """각 서드파티 루트 모듈의 import 위치/가드 여부를 수집."""
    guarded: set[str] = set()  # try/except ImportError 안에서 import 되는 모듈
    lazy: set[str] = set()  # 함수/클래스 내부 import
    toplevel: set[str] = set()
    loc: dict[str, str] = {}

    def root_of(node: ast.Import | ast.ImportFrom) -> str | None:
        if isinstance(node, ast.Import):
            name = node.names[0].name
        else:
            if node.level and node.level > 0:
                return None  # 상대 import = 퍼스트파티
            name = node.module or ""
        if not name:
            return None
        root = name.split(".")[0]
        # ⚠ 절대 import 라도 퍼스트파티가 있다(예: `from app.config import settings`
        # — bilingual_smoke.py 가 backend/ 를 sys.path 에 넣고 사용). 이를 서드파티로
        # 오인하면 `app` 이라는 PyPI 패키지 누락으로 잘못 보고된다(실측 오탐 1건).
        if root in FIRST_PARTY_ROOTS:
            return None
        return root

    # 1) try/except ImportError 내부 import 수집
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            handles_importerror = any(
                isinstance(h.type, ast.Name) and h.type.id == "ImportError" for h in node.handlers
            ) or any(h.type is None for h in node.handlers)
            if handles_importerror:
                for sub in ast.walk(node):
                    if isinstance(sub, (ast.Import, ast.ImportFrom)):
                        r = root_of(sub)
                        if r and not is_stdlib(r):
                            guarded.add(r)
                            loc.setdefault(r, f"L{sub.lineno}")

    # 2) 최상위 vs lazy
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            r = root_of(node)
            if r and not is_stdlib(r):
                toplevel.add(r)
                loc.setdefault(r, f"L{node.lineno}")

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for sub in ast.walk(node):
                if isinstance(sub, (ast.Import, ast.ImportFrom)):
                    r = root_of(sub)
                    if r and not is_stdlib(r) and r not in toplevel:
                        lazy.add(r)
                        loc.setdefault(r, f"L{sub.lineno}")

    result: dict[str, dict] = {}
    for mod in sorted(toplevel | lazy | guarded):
        pip = ALIAS.get(mod, mod)
        if mod in toplevel and mod not in guarded:
            kind = "HARD"
        elif mod in guarded:
            kind = "SOFT(guard)"
        else:
            kind = "SOFT(lazy)"
        result[mod] = {"pip": pip, "kind": kind, "loc": loc.get(mod, "?")}
    return result


def resolve_requirements(argv: list[str]) -> Path:
    """`--requirements PATH` (또는 위치 인자) 로 감사 기준 파일을 바꿀 수 있다."""
    args = [a for a in argv if a != "--"]
    path = DEPLOY
    for i, arg in enumerate(args):
        if arg == "--requirements" and i + 1 < len(args):
            path = Path(args[i + 1])
        elif arg.startswith("--requirements="):
            path = Path(arg.split("=", 1)[1])
    if not path.is_absolute():
        path = _SET_ROOT / path
    return path


def main() -> int:
    global DEPLOY
    DEPLOY = resolve_requirements(sys.argv[1:])
    if not DEPLOY.is_file():
        print(f"[error] requirements 파일을 찾을 수 없다: {DEPLOY}")
        return 2
    deploy = installed_names(DEPLOY)
    app_imports: dict[str, dict] = {}
    for py in sorted(APP.rglob("*.py")):
        if SKIP_PARTS & set(py.parts):
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError as e:  # noqa: PERF203
            print(f"[parse-error] {py}: {e}")
            continue
        rel = py.relative_to(ROOT).as_posix()
        for mod, info in classify(tree).items():
            if mod not in app_imports:
                app_imports[mod] = dict(info, first=rel)
            elif info["kind"] == "HARD":  # HARD 가 최우선(가장 위험)
                app_imports[mod]["kind"] = "HARD"
                app_imports[mod]["loc"] = info["loc"]

    hard_missing, soft_missing = [], []
    for mod, info in sorted(app_imports.items()):
        if norm(info["pip"]) in deploy:
            continue
        (hard_missing if info["kind"] == "HARD" else soft_missing).append((mod, info))

    print(f"{DEPLOY.relative_to(_SET_ROOT).as_posix()} 패키지 수 : {len(deploy)}")
    print(f"backend/app 서드파티 import 모듈  : {len(app_imports)}")
    print()
    print("=" * 78)
    print(f"[HARD] 누락 — 컨테이너 **기동 실패** ({len(hard_missing)}건)")
    print("=" * 78)
    for mod, info in hard_missing:
        print(f"  {mod:22s} pip={info['pip']:22s} {info['first']}:{info['loc']}")

    print()
    print("=" * 78)
    print(f"[SOFT] 누락 — 기동은 되나 **기능 조용히 비활성화** ({len(soft_missing)}건)")
    print("=" * 78)
    for mod, info in soft_missing:
        print(f"  {mod:22s} pip={info['pip']:22s} {info['kind']:11s} {info['first']}:{info['loc']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
