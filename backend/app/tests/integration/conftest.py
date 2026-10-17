"""Explicit clock control for chat cooldown tests, without real-time sleeps."""

import pytest


class ChatClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float = 30.0) -> None:
        self.now += seconds


@pytest.fixture
def chat_clock() -> ChatClock:
    return ChatClock()
