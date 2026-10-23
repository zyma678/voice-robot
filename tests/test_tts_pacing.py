import anyio
import pytest

from voice_robot import server


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self.send_cost = 0.0
        self._clock = None

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append(data)
        if self._clock is not None:
            self._clock["t"] += self.send_cost


def test_hardware_tts_audio_is_split_and_paced_to_realtime() -> None:
    async def scenario() -> None:
        websocket = FakeWebSocket()
        clock = {"t": 0.0}
        websocket._clock = clock
        delays: list[float] = []

        def now() -> float:
            return clock["t"]

        async def fake_sleep(delay: float) -> None:
            delays.append(delay)
            clock["t"] += delay

        # send_bytes is instant: pacing should sleep the audio out to real time.
        await server._send_tts_audio_bytes(
            websocket,
            b"x" * 2500,
            device_id="esp32-001",
            sample_rate_hz=16000,
            frame_bytes=1000,
            sleep_fn=fake_sleep,
            clock=now,
        )

        assert [len(item) for item in websocket.sent] == [1000, 1000, 500]
        # 2500 bytes @ 16kHz/16bit = 2500 / 32000 s of audio; total wall == that.
        assert clock["t"] == pytest.approx(2500 / 32000)
        assert all(d > 0 for d in delays)

    anyio.run(scenario)


def test_hardware_tts_pacing_absorbs_send_overhead() -> None:
    async def scenario() -> None:
        websocket = FakeWebSocket()
        clock = {"t": 0.0}
        websocket._clock = clock
        # Each send "costs" more wall time than the frame's audio budget, so the
        # sender is always behind and must never add a positive sleep.
        websocket.send_cost = 0.05
        delays: list[float] = []

        def now() -> float:
            return clock["t"]

        async def fake_sleep(delay: float) -> None:
            delays.append(delay)
            clock["t"] += delay

        await server._send_tts_audio_bytes(
            websocket,
            b"x" * 2500,
            device_id="esp32-001",
            sample_rate_hz=16000,
            frame_bytes=1000,
            sleep_fn=fake_sleep,
            clock=now,
        )

        assert [len(item) for item in websocket.sent] == [1000, 1000, 500]
        # Overhead (3 × 0.05 = 0.15s) dwarfs the audio budget, so no sleeps fire:
        # total wall is just the unavoidable send cost, never stretched further.
        assert delays == []
        assert clock["t"] == pytest.approx(0.15)

    anyio.run(scenario)


def test_console_tts_audio_is_not_split_or_paced() -> None:
    async def scenario() -> None:
        websocket = FakeWebSocket()
        delays: list[float] = []

        async def fake_sleep(delay: float) -> None:
            delays.append(delay)

        await server._send_tts_audio_bytes(
            websocket,
            b"x" * 2500,
            device_id="web-console",
            sample_rate_hz=16000,
            frame_bytes=1000,
            sleep_fn=fake_sleep,
        )

        assert websocket.sent == [b"x" * 2500]
        assert delays == []

    anyio.run(scenario)
