"""回归测试：上传/下载相关安全加固（第五轮审计）。

- 音色克隆文件头魔数校验（CWE-434 纵深防御：扩展名可被改名绕过，需校验内容）
- 下载文件名 Content-Disposition 头注入防护（CWE-113）
"""

from app.routers import songs


def test_vocal_clone_magic_ok_mp3_frame_sync():
    # MPEG 音频帧同步: 0xFF 后跟 0b111xxxxx（11 位全 1）
    data = b"\xff\xfb\x90\x00" + b"\x00" * 64
    assert songs._VOCAL_CLONE_MAGIC_OK(data) is True


def test_vocal_clone_magic_ok_id3():
    assert songs._VOCAL_CLONE_MAGIC_OK(b"ID3\x03\x00\x00" + b"\x00" * 32) is True


def test_vocal_clone_magic_ok_m4a_ftyp():
    # ISO BMFF: [size][ftyp][major_brand ...]
    assert songs._VOCAL_CLONE_MAGIC_OK(b"\x00\x00\x00\x20ftypmp42" + b"\x00" * 32) is True


def test_vocal_clone_magic_ok_wav():
    assert songs._VOCAL_CLONE_MAGIC_OK(b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 16) is True


def test_vocal_clone_magic_rejects_non_audio():
    assert songs._VOCAL_CLONE_MAGIC_OK(b"<html>not audio</html>") is False
    assert songs._VOCAL_CLONE_MAGIC_OK(b"") is False
    assert songs._VOCAL_CLONE_MAGIC_OK(b"\x00" * 8) is False


def test_safe_download_filename_strips_injection():
    # 危险字符（引号/控制符/路径分隔符）被移除，不再注入 Content-Disposition 头
    assert songs._safe_download_filename('a"b\r\nc/d\\e') == "abcde"
    assert songs._safe_download_filename("  my song  ") == "my_song"
    assert songs._safe_download_filename(None) == "song"
    # 限长保护
    assert len(songs._safe_download_filename("x" * 200)) <= 80


def test_vocal_clone_disabled_by_default():
    """⏸ 产品暂停（2026-09-13）：出厂默认必须关闭 → 端点 501，防误上线。

    实现与测试**仍保留**（本文件魔数用例、stage0/stage2 的克隆用例在测试环境显式开启后覆盖），
    仅入口关闭；重新上线只需 `ENABLE_VOCAL_CLONE=true`。
    """
    from app.config import Settings

    assert Settings().enable_vocal_clone is False
