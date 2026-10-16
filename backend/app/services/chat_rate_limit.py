"""Process-local, atomic cooldown shared by all HTTP chat entrypoints."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from math import ceil
from threading import Lock
from time import monotonic


class ChatRateLimitExceeded(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__("请求过于频繁，请稍后重试。每个客户端及来源 IP 每 30 秒只能请求一次聊天。")
        self.retry_after = retry_after


class ChatRateLimiter:
    """Reserve both identities together; rejections never extend the cooldown.

    Uses a monotonic clock and discards expired identities on every request.
    State belongs to one application process, just like the active run registry.
    """

    def __init__(self, *, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        self._expires: OrderedDict[tuple[str, str], float] = OrderedDict()
        self._lock = Lock()

    def acquire(self, *, client_id: str, source_ip: str) -> None:
        keys = (("client", client_id), ("ip", source_ip))
        with self._lock:
            now = self._clock()
            while self._expires: # 清理过期expires
                first = next(iter(self._expires))
                if self._expires[first] > now:
                    break
                self._expires.popitem(last=False)
            remaining = max(self._expires.get(key, now) - now for key in keys)
            if remaining > 0: # 如果还在并发限制时间内，抛出error
                raise ChatRateLimitExceeded(ceil(remaining))
            for key in keys:
                self._expires[key] = now + 30.0
