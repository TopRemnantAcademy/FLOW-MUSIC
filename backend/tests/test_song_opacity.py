"""引擎名/模型版本不在公共响应泄露（P0 防回归）。

依据：用户 09:32 硬约束「前端页面不显示引擎名，仅后台/管理员可见」。
- SongOut（公共）不得序列化 engine_name / model_version
- AdminSongOut（后台）必须保留两者
- 前端公共 SongView 类型不含 engine_name；AdminSongView 含
"""

import re
import pathlib

from app.schemas import SongOut, AdminSongOut


def test_songout_has_no_engine_or_model_fields():
    out = SongOut(id=1, title="t", status="completed", created_at="2026-01-01T00:00:00")
    dumped = out.model_dump()
    assert "engine_name" not in dumped, "engine_name 不应出现在公共 SongOut"
    assert "model_version" not in dumped, "model_version 不应出现在公共 SongOut"


def test_adminsongout_retains_engine_and_model_fields():
    out = AdminSongOut(
        id=1,
        title="t",
        status="completed",
        created_at="x",
        user_email="a@b.c",
        engine_name="mureka-9",
        model_version="mureka-9",
    )
    dumped = out.model_dump()
    assert dumped["engine_name"] == "mureka-9"
    assert dumped["model_version"] == "mureka-9"
    assert dumped["user_email"] == "a@b.c"


def test_frontend_songview_no_engine_name():
    api_ts = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "lib" / "api.ts"
    content = api_ts.read_text(encoding="utf-8")
    m = re.search(r"export type SongView = \{(.*?)\n\};", content, re.S)
    assert m, "公共 SongView 类型未找到"
    assert "engine_name" not in m.group(1), "公共 SongView 不应含 engine_name"
    m2 = re.search(r"export type AdminSongView = SongView & \{(.*?)\n\};", content, re.S)
    assert m2, "AdminSongView 类型未找到"
    assert "engine_name" in m2.group(1), "AdminSongView 应含 engine_name"
