"""공존(co-location) fault-injection 테스트 더블 (T8.6 네트워크 분할 시뮬레이션).

실제 네트워크/외부 서비스를 건드리지 않고 **지연·실패·분할** 을 주입한다.
러턴타임 측정·경계 타임아웃·지수 백오프 재시도 로직을 검증하는 테스트에서
실제 호출을 이 더블로 감싸다.

사용:
    inj = LatencyInjector(latency_ms=2000, fail_with=ConnectionError)
    await inj(lambda: some_async_fetch())   # 2s 지연 후 ConnectionError
    inj.clear()                              # 분할 해제 → 정상 복귀
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable


class LatencyInjector:
    """비동기 콜러블 팩토리에 지연/실패를 주입하는 더블.

    ``coro_factory`` 는 인자 없는 콜러블이며 awaitable 을 반환해야 한다.
    주입된 ``latency_ms`` 만큼 대기한 뒤, ``fail_with`` 가 설정되어 있으면 해당
    예외를 발생시킨다(경계 분할 시뮬레이션). 그 외에는 팩토리의 결과를 그대로 반환.
    """

    def __init__(self, latency_ms: float = 0.0, fail_with=None):
        self.latency_ms = float(latency_ms)
        self.fail_with = fail_with
        self.calls = 0
        self.total_injected_ms = 0.0

    async def __call__(self, coro_factory: Callable[[], Awaitable]):
        self.calls += 1
        if self.latency_ms:
            await asyncio.sleep(self.latency_ms / 1000.0)
            self.total_injected_ms += self.latency_ms
        if self.fail_with is not None:
            raise self.fail_with
        return await coro_factory()

    def simulate_partition(self) -> None:
        """경계 분할 시뮬레이션 — 이후 호출은 ``ConnectionError`` 를 유발."""
        self.fail_with = ConnectionError

    def clear(self) -> None:
        """주입 해제(분할 복구)."""
        self.fail_with = None
        self.latency_ms = 0.0
