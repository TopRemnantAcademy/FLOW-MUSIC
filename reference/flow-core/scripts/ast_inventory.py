"""AST-level capability inventory for FLOW + ai-music-platform backends.

Purpose: avoid "reinvention" (RC-E) and expose auditable structure (symbols,
not strings) for defect hunting. Prints a compact inventory.

Usage:
  python scripts/ast_inventory.py flow   # FLOW backend only
  python scripts/ast_inventory.py music  # ai-music-platform backend only
  python scripts/ast_inventory.py all
  python scripts/ast_inventory.py all --routes --globals
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

FLOW_ROOT = Path(__file__).resolve().parents[1]
MUSIC_ROOT = Path("C:/Desktop/korean-gospel-ai/ai-music-platform")

TARGETS = {
    "flow": FLOW_ROOT / "backend" / "app",
    "music": MUSIC_ROOT / "backend" / "app",
}

# Patterns worth surfacing during a defect audit.
INTERESTING_CALLS = {
    "compare_digest",
    "token_hex",
    "token_urlsafe",
    "randbelow",
    "random",
    "create_task",
    "ensure_future",
    "Thread",
    "Lock",
    "RLock",
    "Semaphore",
    "BackgroundTasks",
    "add_task",
    "apscheduler",
    "BackgroundScheduler",
    "utcnow",
    "now",
    "time",
    "sleep",
    "exec",
    "eval",
    "subprocess",
    "verify",
    "hmac",
    "jwt",
    "encode",
    "decode",
}
INTERESTING_NAMES = {
    "compare_digest",
    "hmac",
    "secrets",
    "random",
    "Lock",
    "RLock",
    "BackgroundTasks",
    "AsyncIOScheduler",
    "BackgroundScheduler",
}
MUTABLE_GLOBAL_TYPES = {"Dict", "List", "Set", "dict", "list", "set", "defaultdict", "OrderedDict"}


def _decorator_name(d: ast.expr) -> str:
    if isinstance(d, ast.Name):
        return d.id
    if isinstance(d, ast.Attribute):
        return f"{_decorator_name(d.value)}.{d.attr}"
    if isinstance(d, ast.Call):
        return _decorator_name(d.func) + "()"
    return type(d).__name__


def _unparse(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover
        return "<unparse-error>"


class ModuleReport:
    def __init__(self, rel: str, path: Path):
        self.rel = rel
        self.path = path
        self.classes: list[tuple[str, int, list[str]]] = []
        self.funcs: list[tuple[str, int, list[str]]] = []
        self.routes: list[tuple[int, str, str]] = []
        self.module_assigns: list[tuple[int, str, str]] = []
        self.mutable_globals: list[tuple[int, str, str]] = []
        self.hits: list[tuple[int, str]] = []
        self.lines = 0


def analyze(path: Path, root: Path, want_routes: bool, want_globals: bool) -> ModuleReport:
    src = path.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(src, filename=str(path))
    rel = path.relative_to(root).as_posix()
    rep = ModuleReport(rel, path)
    rep.lines = len(src.splitlines())

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            rep.classes.append(
                (
                    node.name,
                    node.lineno,
                    [b.id if isinstance(b, ast.Name) else _unparse(b) for b in node.bases],
                )
            )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            decs = [_decorator_name(d) for d in node.decorator_list]
            rep.funcs.append((node.name, node.lineno, decs))
            for d in node.decorator_list:
                txt = _decorator_name(d)
                if txt.startswith(("router.", "app.", "api_router.")) and any(
                    m in txt
                    for m in (
                        ".get",
                        ".post",
                        ".put",
                        ".patch",
                        ".delete",
                        ".api_route",
                        ".websocket",
                    )
                ):
                    rep.routes.append((node.lineno, txt, node.name))
        elif isinstance(node, ast.Call):
            fn = node.func
            name = (
                fn.attr
                if isinstance(fn, ast.Attribute)
                else (fn.id if isinstance(fn, ast.Name) else "")
            )
            if name in INTERESTING_CALLS:
                rep.hits.append((node.lineno, f"call:{name}"))
        elif isinstance(node, ast.Name) and node.id in INTERESTING_NAMES:
            rep.hits.append((node.lineno, f"name:{node.id}"))

    if want_globals:
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    if isinstance(t, ast.Name):
                        val = _unparse(node.value)
                        rep.module_assigns.append((node.lineno, t.id, val[:80]))
                        head = val.split("(")[0].split("[")[0].strip()
                        if head in MUTABLE_GLOBAL_TYPES or head.startswith(
                            ("_cache", "_registry", "_state")
                        ):
                            rep.mutable_globals.append((node.lineno, t.id, head))

    rep.classes.sort(key=lambda x: x[1])
    rep.funcs.sort(key=lambda x: x[1])
    return rep


def render(tag: str, root: Path, want_routes: bool, want_globals: bool) -> None:
    files = sorted(p for p in root.rglob("*.py"))
    print(f"\n{'=' * 72}\n[{tag}] root={root}  files={len(files)}\n{'=' * 72}")
    total_lines = 0
    for p in files:
        try:
            rep = analyze(p, root, want_routes, want_globals)
        except SyntaxError as e:
            print(f"!! SYNTAX ERROR {p}: {e}")
            continue
        total_lines += rep.lines
        print(f"\n--- {tag}/{rep.rel} ({rep.lines} lines)")
        if rep.classes:
            for name, ln, bases in rep.classes:
                print(f"    class {name}:{ln} ({', '.join(bases)})")
        if rep.routes:
            for ln, dec, fname in rep.routes:
                print(f"    ROUTE {ln}: {dec} -> {fname}")
        if want_globals and rep.mutable_globals:
            for ln, name, typ in rep.mutable_globals:
                print(f"    GLOBAL-MUTABLE {ln}: {name} = {typ}(...)")
        if rep.hits:
            seen = set()
            for ln, h in sorted(rep.hits):
                if h in seen:
                    continue
                seen.add(h)
                print(f"    HIT {ln}: {h}")
    print(f"\n[{tag}] total python lines = {total_lines}")


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    want_routes = "--routes" in sys.argv
    want_globals = "--globals" in sys.argv
    sel = args[0] if args else "all"
    for tag in ["flow", "music"] if sel == "all" else [sel]:
        render(tag, TARGETS[tag], want_routes, want_globals)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
