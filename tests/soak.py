#!/usr/bin/env python3
"""Долгий прогон стабильности моста (запускается вручную, не в CI).

Симулирует сессию, в которой канал «умирает» каждые N ответов, и проверяет,
что Bridge самовосстанавливается: операции продолжаются, задачи не текут,
pending не накапливается, память стабильна.

Запуск:
    uv run python tests/soak.py --minutes 5
"""
import argparse
import asyncio
import gc
import sys
import time
import tracemalloc
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from bridge import Bridge, gaia_frame  # noqa: E402
from gaia_transport import spp_frame  # noqa: E402


class FlakyTransport:
    """Авто-ack транспорт; каждые fail_every получений канал «умирает»."""

    fail_every = 25

    def __init__(self):
        self.received = 0
        self.sent = 0
        self.closed = False
        self.alive = True
        self.queue = asyncio.Queue()

    async def connect(self):
        pass

    async def send(self, frame):
        self.sent += 1
        command = int.from_bytes(frame[2:4], "big")
        self.queue.put_nowait(spp_frame(gaia_frame(command | 0x0100, self._payload(command))))

    @staticmethod
    def _payload(command):
        if command == 0x1A05:
            return b"\x01"                       # ANC on
        if command == 0x1A01:
            return b"\x01\x02\x02\x00\x03\x01"   # custom, antiwind 2
        if command == 0x1A03:
            return b"\x28"                       # transparency 40
        if command == 0x1805:
            return b"\x00"                       # transparent hearing off
        return b""

    async def recv_frame(self, timeout=3.0):
        self.received += 1
        if self.received % self.fail_every == 0:
            raise OSError("simulated channel death")
        try:
            return await asyncio.wait_for(self.queue.get(), timeout)
        except asyncio.TimeoutError:
            return b""

    async def close(self):
        self.closed = True
        self.alive = False

    def is_alive(self):
        return self.alive


async def soak(minutes: int) -> int:
    tracemalloc.start()
    transports = []

    def factory(_addr):
        transport = FlakyTransport()
        transports.append(transport)
        return transport

    reconnected = []
    bridge = Bridge(lambda event: reconnected.append(1) if event.get("event") == "reconnected" else None)
    with patch("bridge.create_transport", side_effect=factory), \
         patch("bridge.BACKGROUND_RECONNECT_START", 0.2), \
         patch("bridge.BACKGROUND_RECONNECT_MAX", 1.0):
        await bridge.cmd_connect("AA:BB:CC:DD:EE:FF")
        deadline = time.monotonic() + minutes * 60
        operations = 0
        while time.monotonic() < deadline:
            try:
                await bridge.cmd_get()
                operations += 1
            except RuntimeError as error:
                # Полный отказ состояния тоже допустим в момент смерти канала,
                # но должен исчезать сразу после восстановления.
                print(f"get полностью не удался (перезапись после восстановления): {error}")
            await asyncio.sleep(0.05)
        await bridge.cmd_close()

    gc.collect()
    current, peak = tracemalloc.get_traced_memory()
    alive_tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    print(f"успешных get: {operations}, восстановлений канала: {len(reconnected)}")
    print(f"транспортов создано: {len(transports)} (смерть каждые {FlakyTransport.fail_every} ответов)")
    print(f"tracemalloc: current={current / 1024:.0f} KiB, peak={peak / 1024:.0f} KiB")
    print(f"активных задач после close: {len(alive_tasks)} (watchdog ожидаем)")
    if bridge._pending:
        print(f"ПРОВАЛ: pending не пуст: {list(bridge._pending)}")
        return 1
    if len(alive_tasks) > 3:
        print("ПРОВАЛ: слишком много оставшихся задач (утечка)")
        return 1
    if len(reconnected) == 0:
        print("ПРОВАЛ: сбои канала не случились — прогон не покрыл восстановление")
        return 1
    if operations < len(reconnected):
        print("ПРОВАЛ: восстановление не поспевает за сбоями")
        return 1
    print("ИТОГ: стабильно")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Soak-прогон стабильности моста")
    parser.add_argument("--minutes", type=float, default=5.0, help="длительность прогона в минутах")
    args = parser.parse_args()
    sys.exit(asyncio.run(soak(args.minutes)))


if __name__ == "__main__":
    main()
