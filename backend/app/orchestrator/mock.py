"""Mock 音乐供应商：不调用任何真实 API，用于本地开发与测试。

完全规避「Suno 真实字段未核实」的幻觉风险——这里只用已定义好的
GenerateRequest / SongResult 契约，返回固定假数据，任何字段都不臆造。
"""

from app.orchestrator.base import GenerateRequest, MusicProvider, SongResult


class MockProvider(MusicProvider):
    # Mock 放行全部 task：其 create() 不区分 task，但契约上声明全支持，
    # 以免既有 extend/cover/remix 测试用例被阶段 0 的 fail-closed(501) 拦掉。
    supported_tasks: frozenset[str] = frozenset({"generate", "custom", "extend", "cover", "remix"})

    def __init__(self, first_get_processing: bool = False):
        # 若为 True，第一次 get 返回 processing（模拟轮询中间态），第二次返回完成
        self.first_get_processing = first_get_processing
        self._get_calls = 0
        self.last_continue_song_id = None  # 记录最近一次 create 收到的续写基准（供测试断言）

    async def create(self, req: GenerateRequest) -> SongResult:
        self.last_continue_song_id = req.continue_song_id
        return SongResult(
            external_id="mock-task-0001",
            custom_id="mock-custom-0001",
            status="processing",
        )

    async def get(self, external_id: str) -> SongResult:
        self._get_calls += 1
        if self.first_get_processing and self._get_calls == 1:
            return SongResult(external_id=external_id, status="processing")
        return SongResult(
            external_id=external_id,
            custom_id="mock-custom-0001",
            status="completed",
            audio_url="https://mock.cdn.example.com/audio.mp3",
            cover_url="https://mock.cdn.example.com/cover.jpg",
            lyric="mock line one\nmock line two",
            title="Mock Song",
            model_version="chirp-v4.5",
            duration=120,
        )

    def _fake(self, **kw) -> "SongResult":
        return SongResult(
            external_id="mock-pp-task",
            custom_id="mock-custom-0001",
            status="completed",
            audio_url=kw.get("audio_url", "https://mock.cdn.example.com/pp.mp3"),
            cover_url="https://mock.cdn.example.com/cover.jpg",
            lyric=kw.get("lyric", "mock line"),
            title="Mock PP",
            model_version="chirp-v4.5",
            duration=120,
        )

    # 后期处理 / 衍生桩（与 SunoProvider 方法对齐，避免调用 AttributeError）
    async def crop(self, clip_id, start_time, end_time):
        return self._fake()

    async def speed(self, clip_id, speed):
        return self._fake()

    async def whole_song(self, clip_id):
        return self._fake()

    async def aligned_lyrics(self, lyrics, suno_id):
        return self._fake(lyric=f"[00:00] {lyrics}")

    async def upload(self, audio_url):
        return "mock-custom-upload-1"

    async def sound(self, title, tags, mv="chirp-crow", tempo=None, key=None, loop=False):
        return self._fake()
