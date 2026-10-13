"""GAP-040 应用内更新日志：接口契约 + 版本号与前端同源。

钉住的契约：

1. **版本号只有一处真相**：`frontend/package.json` 的 version 必须等于
   本模块的 `CURRENT_VERSION`。仓库里已经出现过「package.json 写 0.1.0、
   CHANGELOG 根本没这一版」的漂移 —— 那种情况下「当前版本」展示出来就是假的。
2. **不解析的版本号不报「有更新」**：`app_version=1.0`/`v9.9.9`/空值都不得触发
   `has_update=true`（误报会让用户白白刷新一次，且无从解释）。
3. **端点是公开的**：更新日志是给未登录用户看的产品说明，不能要求认证。
4. **要点必须带出处**：每条 note 要么带 (#NN) 要么带提交 hash；
   写不出来的内容说明没核对过，宁可少写。
"""

import json
import re
from pathlib import Path

from app.routers import release as release_router

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_PKG = REPO_ROOT / "frontend" / "package.json"

_REF_RE = re.compile(r"#\d+|[0-9a-f]{7,40}")


def test_current_version_matches_frontend_package_json():
    """后端声明的版本 == 前端 package.json 的版本（同一真相，不许两处各写各的）。"""
    assert FRONTEND_PKG.exists(), f"找不到 {FRONTEND_PKG}"
    pkg = json.loads(FRONTEND_PKG.read_text(encoding="utf-8"))
    assert pkg["version"] == release_router.CURRENT_VERSION, (
        f"版本号漂移：frontend/package.json={pkg['version']}，"
        f"release.py CURRENT_VERSION={release_router.CURRENT_VERSION}"
    )


def test_notes_are_sourced_and_dated():
    """要点必须有出处；日期必须是 YYYY-MM-DD 且不晚于今天（不许预填未来日期）。"""
    import datetime

    today = datetime.date.today().isoformat()
    assert release_router.RELEASE_NOTES, "RELEASE_NOTES 为空"
    for rel in release_router.RELEASE_NOTES:
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", rel["date"]), rel["date"]
        assert rel["date"] <= today, f"发布日 {rel['date']} 晚于今天 {today}（不许预填）"
        assert rel["entries"], f"{rel['version']} 没有任何要点"
        for e in rel["entries"]:
            assert e.get("ref"), f"条目缺出处：{e}"
            assert _REF_RE.search(e["ref"]), f"出处格式无法追溯：{e['ref']}"


def test_release_notes_endpoint_is_public_and_well_shaped(client):
    r = client.get("/api/release/notes")
    assert r.status_code == 200, r.text  # 未登录也可见
    body = r.json()
    assert body["app_version"] == release_router.CURRENT_VERSION
    assert body["latest_version"] == release_router.CURRENT_VERSION
    assert body["has_update"] is False  # 缺省无可比对象 → 不报有更新
    assert body["channel"] == "stable"
    assert isinstance(body["notes"], list) and body["notes"]


def test_has_update_only_when_strictly_older_and_parseable(client):
    # 更旧的有效版本 → True
    r = client.get("/api/release/notes", params={"app_version": "0.1.0"})
    assert r.json()["has_update"] is True

    # 同级 / 更新 → False
    assert client.get("/api/release/notes", params={"app_version": "0.2.0"}).json()["has_update"] is False
    assert client.get("/api/release/notes", params={"app_version": "9.9.9"}).json()["has_update"] is False

    # 无法严格解析 → 一律 False（不猜、不误报）
    for bad in ("1.0", "v0.1.0", "0.1.0-rc1", "abc", ""):
        got = client.get("/api/release/notes", params={"app_version": bad}).json()
        assert got["has_update"] is False, f"{bad!r} 不该被判为有更新"


def test_latest_notes_carry_refs(client):
    """下发给客户端的每条要点都要能追溯（(#NN) 或 7 位以上 hash）。"""
    notes = client.get("/api/release/notes").json()["notes"]
    for n in notes:
        assert _REF_RE.search(n), f"要点无出处：{n}"
