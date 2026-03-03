from __future__ import annotations

import pytest

from tele_ai import bot_mode


class _FakeUpdater:
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self.running = False

    async def start_polling(self, *, drop_pending_updates: bool) -> None:
        self._events.append(f"start_polling:{drop_pending_updates}")
        self.running = True

    async def stop(self) -> None:
        self._events.append("updater.stop")
        self.running = False


class _FakeApp:
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self.running = False
        self.updater = _FakeUpdater(events)

    async def initialize(self) -> None:
        self._events.append("app.initialize")

    async def start(self) -> None:
        self._events.append("app.start")
        self.running = True

    async def stop(self) -> None:
        self._events.append("app.stop")
        self.running = False

    async def shutdown(self) -> None:
        self._events.append("app.shutdown")


class _FakeRuntime:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def post_init(self, app: _FakeApp) -> None:
        assert app is not None
        self._events.append("runtime.post_init")

    async def post_shutdown(self, app: _FakeApp) -> None:
        assert app is not None
        self._events.append("runtime.post_shutdown")


@pytest.mark.asyncio
async def test_run_bot_async_initializes_runtime_before_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    fake_app = _FakeApp(events)
    fake_runtime = _FakeRuntime(events)

    monkeypatch.setattr(bot_mode, "_build_bot_app", lambda settings: (fake_app, fake_runtime))

    app, runtime = await bot_mode.run_bot_async(settings=object())  # type: ignore[arg-type]

    assert app is fake_app
    assert runtime is fake_runtime
    assert events == [
        "app.initialize",
        "runtime.post_init",
        "app.start",
        "start_polling:True",
    ]


@pytest.mark.asyncio
async def test_stop_bot_async_stops_and_shuts_down_runtime() -> None:
    events: list[str] = []
    fake_app = _FakeApp(events)
    fake_runtime = _FakeRuntime(events)
    fake_app.running = True
    fake_app.updater.running = True

    await bot_mode.stop_bot_async(fake_app, fake_runtime)  # type: ignore[arg-type]

    assert events == [
        "updater.stop",
        "app.stop",
        "runtime.post_shutdown",
        "app.shutdown",
    ]
