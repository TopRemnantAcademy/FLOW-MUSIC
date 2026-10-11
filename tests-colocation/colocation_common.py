"""공존(co-location) mocktest 공용 정적 분석 헬퍼 (재발명 금지).

단일 소스: D1~D6 각 파일이 동일 헬퍼를 인라인 복제하던 것을 중앙화.
신규 co-location 테스트(D7/D8 등)는 본 모듈에서만 가져온다.

음악 플랫폼 저장소는 FLOW 밖에 있으므로 환경변수로 경로를 받는다.
    export MUSIC_PLATFORM_REPO=C:/Desktop/korean-gospel-ai/ai-music-platform
미설정 시 음악 의존 테스트는 skip 된다(게이트 강등 방지, SC-24).

음악 모듈은 AST/정적 검사 로만 다루므로 sys.modules["app"] 오염(SC-20)을 회피한다
— 런타임 import 없이 소스 트리를 파싱한다(음악 app 모듈명이 FLOW 와 동일 'app' 이어서
충돌 위험이 크므로 절대 import 하지 않는다).
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _music_backend() -> Path:
    """음악 플랫폼 backend 경로. 미설정이면 skip."""
    raw = os.environ.get("MUSIC_PLATFORM_REPO", "").strip()
    if not raw:
        pytest.skip(
            "MUSIC_PLATFORM_REPO 환경변수 미설정 — 음악 플랫폼 저장소 경로를 지정하세요. "
            "(예: C:/Desktop/korean-gospel-ai/ai-music-platform)"
        )
    backend = Path(raw) / "backend"
    if not (backend / "alembic.ini").is_file():
        pytest.skip(f"MUSIC_PLATFORM_REPO 하위에 backend/alembic.ini 가 없다: {backend}")
    return backend


def _mread(rel: str) -> str:
    """음악 repo 상대경로 소스 읽기(미설정 시 skip)."""
    return (_music_backend() / rel).read_text(encoding="utf-8", errors="replace")


def _mast(rel: str) -> ast.Module:
    """음악 repo 상대경로 AST 파싱."""
    return ast.parse(_mread(rel))


def _flow_read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _flow_ast(path: Path) -> ast.Module:
    return ast.parse(_flow_read(path))


def _assign_target_names(tree: ast.Module) -> dict[str, ast.AST]:
    """모듈 최상위 name = ... / name: T = ... 매핑(클래스 내 필드 제외)."""
    out: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = node
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out[node.target.id] = node
    return out


def _func_node(tree: ast.Module, name: str):
    """동기/비동기 함수 정의 노드 탐색."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _call_name(call: ast.Call) -> str:
    """Call 대상 이름(``a.b()`` → "b", ``f()`` → "f")."""
    f = call.func
    if isinstance(f, ast.Attribute):
        return f.attr
    return getattr(f, "id", "")


def _call_names_in(node) -> set[str]:
    """노드 내부의 모든 Call 대상 이름 집합."""
    names: set[str] = set()
    for c in ast.walk(node):
        if isinstance(c, ast.Call):
            names.add(_call_name(c))
    return names


def _collect_routes(tree: ast.Module) -> list[tuple[str, str]]:
    """``@router.<method>(\"path\")`` 데코레이터에서 (method, path) 추출.

    method ∈ get/post/put/delete/patch.
    """
    routes: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                method = dec.func.attr
                if (
                    method in ("get", "post", "put", "delete", "patch")
                    and dec.args
                    and isinstance(dec.args[0], ast.Constant)
                    and isinstance(dec.args[0].value, str)
                ):
                    routes.append((method, dec.args[0].value))
    return routes


def _name_used_in(node, name: str) -> bool:
    """서브트리 내 어디서든 ``name`` 이름 참조(Attribute 값 아님, 순수 Name) 있는지."""
    return any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(node))


def _assigns_attr_in(tree: ast.Module, attr: str) -> bool:
    """모듈 내 어디서든 ``<x>.<attr> = ...`` 형태의 대입이 있는지."""
    for n in ast.walk(tree):
        if isinstance(n, (ast.Assign, ast.AugAssign)):
            tgt = n.targets[0] if isinstance(n, ast.Assign) else n.target
            if isinstance(tgt, ast.Attribute) and tgt.attr == attr:
                return True
    return False


def _class_has_column_attr(tree: ast.Module, classname: str, fieldname: str) -> bool:
    """클래스 정의 내에 ``fieldname = Column(...)`` 또는 ``fieldname: T = mapped_column(...)`` 가 있는지."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == classname:
            for stmt in node.body:
                if isinstance(stmt, ast.Assign):
                    for t in stmt.targets:
                        if (
                            isinstance(t, ast.Name)
                            and t.id == fieldname
                            and isinstance(stmt.value, ast.Call)
                            and _call_name(stmt.value) in ("Column", "mapped_column")
                        ):
                            return True
                elif (
                    isinstance(stmt, ast.AnnAssign)
                    and isinstance(stmt.target, ast.Name)
                    and stmt.target.id == fieldname
                    and isinstance(stmt.value, ast.Call)
                    and _call_name(stmt.value) in ("Column", "mapped_column")
                ):
                    return True
    return False


def _has_if_not_var_continue(func, varname: str) -> bool:
    """func 내 ``if not <varname>: continue`` 존재 여부(F5 침묵 소멸 증상 핀)."""
    for n in ast.walk(func):
        if (
            isinstance(n, ast.If)
            and isinstance(n.test, ast.UnaryOp)
            and isinstance(n.test.op, ast.Not)
            and isinstance(n.test.operand, ast.Name)
            and n.test.operand.id == varname
            and any(isinstance(b, ast.Continue) for b in n.body)
        ):
            return True
    return False
