"""F5 MusicGen 供应商测试：工厂识别 + 缺依赖/无 GPU 时清晰报错（不造桩）。"""

import asyncio

import pytest

from app.config import settings
from app.orchestrator.base import GenerateRequest


def test_factory_recognizes_musicgen():
    import app.orchestrator.client as cl
    from app.orchestrator.client import get_orchestrator, set_orchestrator
    from app.orchestrator.mock import MockProvider
    from app.orchestrator.musicgen import MusicGenProvider

    set_orchestrator(None)  # 清除测试注入的 mock 覆盖，才能走到真实工厂构造
    try:
        p = get_orchestrator("musicgen")
        assert isinstance(p, MusicGenProvider)
    finally:
        set_orchestrator(MockProvider())  # 还原 conftest 注入，避免影响其它测试


def test_musicgen_create_requires_gpu():
    from app.orchestrator.musicgen import MusicGenProvider

    p = MusicGenProvider()
    with pytest.raises(RuntimeError):
        asyncio.run(p.create(GenerateRequest(prompt="calm piano")))
