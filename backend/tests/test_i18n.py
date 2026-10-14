"""GAP-017 后端 i18n 辅助纯函数测试（不依赖 DB / app 启动）。"""
from __future__ import annotations

import pytest

from app import i18n


def test_pick_message_all_locales():
    assert i18n.pick_message(i18n.SONG_NOT_FOUND, "zh") == "歌曲不存在"
    assert i18n.pick_message(i18n.SONG_NOT_FOUND, "en") == "Song not found"
    assert i18n.pick_message(i18n.SONG_NOT_FOUND, "ko") == "곡을 찾을 수 없습니다"


def test_normalize_lang_accepts_bcp47_and_underscore():
    assert i18n.normalize_lang("zh-CN") == "zh"
    assert i18n.normalize_lang("ko_KR") == "ko"
    assert i18n.normalize_lang("EN-us") == "en"


def test_normalize_lang_unknown_falls_back_to_default():
    # 未知语言绝不抛错，一律回默认（防止 keyerror）。
    for bogus in (None, "", "ja", "fr-FR", "  "):
        assert i18n.normalize_lang(bogus) == i18n.DEFAULT_LANG


def test_pick_message_falls_back_to_chinese_for_unknown_lang():
    # 未知语言 → 用 zh 词条，而不是码本身。
    assert i18n.pick_message(i18n.AUTH_REQUIRED, "ja") == "请先登录"


def test_pick_message_unknown_code_uses_default_then_code():
    assert i18n.pick_message("nope.code", "en", default="兜底") == "兜底"
    # 既无词条也无 default → 回退码本身（可见即可定位）。
    assert i18n.pick_message("nope.code", "en") == "nope.code"


def test_pick_message_formats_params():
    assert i18n.pick_message("nope.code", "zh", default="你好 {name}", name="世界") == "你好 世界"


def test_safe_format_missing_param_keeps_template():
    # 缺参数不该 500：原样返回模板。
    assert i18n.pick_message("nope.code", "zh", default="你好 {name}") == "你好 {name}"


def test_safe_format_invalid_braces_keeps_template():
    assert i18n.pick_message("nope.code", "zh", default="bad {") == "bad {"


def test_every_registered_code_has_all_three_locales():
    # 防漂移：登记在表中却没补齐三语 → 该语言用户会看到回退文案。
    for code in i18n.ERROR_CODES:
        entry = i18n.MESSAGES.get(code)
        assert entry is not None, f"错误码未登记词条: {code}"
        for lang in i18n.LOCALES:
            assert entry.get(lang), f"{code} 缺少 {lang} 词条"


def test_available_codes_matches_error_codes():
    assert set(i18n.available_codes()) == set(i18n.ERROR_CODES)


def test_messages_are_pure_module_level_data():
    # 常量表是 Final 语义的数据；这里钉死「可迭代且非空」，防误改成生成器。
    assert len(i18n.MESSAGES) == len(i18n.ERROR_CODES)
    assert isinstance(i18n.LOCALES, tuple)
