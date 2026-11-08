#!/usr/bin/env python3
"""Захват нотификаций с реальных наушников через мост.

Подключается к устройству, проверяет базовые команды и N секунд печатает
всё, что присылает бридж (включая события notification с сырыми payload).

Запуск:
    uv run python tests/capture_notifications.py --seconds 60
    uv run python tests/capture_notifications.py --address AA:BB:CC:DD:EE:FF
"""
import argparse
import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description="Захват нотификаций Momentum 4")
    parser.add_argument("--address", help="Bluetooth-адрес (по умолчанию — поиск MOMENTUM)")
    parser.add_argument("--seconds", type=float, default=60, help="окно прослушивания нотификаций")
    args = parser.parse_args()

    process = subprocess.Popen(
        [sys.executable, "-u", "bridge.py"],
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )

    lines: "queue.Queue[str | None]" = queue.Queue()
    finished = threading.Event()

    def pump_stdout():
        for line in process.stdout:
            lines.put(line.rstrip("\n"))
        lines.put(None)
        finished.set()

    threading.Thread(target=pump_stdout, daemon=True).start()

    def pump_stderr():
        for line in process.stderr:
            print(f"  [stderr] {line.rstrip()}")

    threading.Thread(target=pump_stderr, daemon=True).start()

    def send(message):
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

    def read_reply(expected_id, timeout=70):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = lines.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                break
            if line is None:
                raise RuntimeError("bridge closed stdout")
            message = json.loads(line)
            if "event" in message:
                print(f"[event] {json.dumps(message, ensure_ascii=False)}")
                continue
            if message.get("id") == expected_id:
                return message
        raise TimeoutError(f"нет ответа на {expected_id}")

    line = lines.get(timeout=30)
    assert line is not None and json.loads(line) == {"event": "ready"}, line
    print("[ok] мост готов")

    address = args.address
    if not address:
        send({"id": "list", "cmd": "list"})
        reply = read_reply("list")
        devices = reply.get("result") or []
        for device in devices:
            print(f"[device] {device['name']} — {device['address']}")
        candidates = [d for d in devices if "MOMENTUM" in d["name"].upper()]
        if not candidates:
            print("[stop] наушники не найдены в списке сопряжённых")
            process.stdin.close()
            process.wait(timeout=5)
            return 1
        address = candidates[0]["address"]

    print(f"[connect] {address}")
    send({"id": "connect", "cmd": "connect", "addr": address})
    reply = read_reply("connect")
    if not reply.get("ok"):
        print(f"[stop] подключение не удалось: {reply.get('error')}")
        process.stdin.close()
        process.wait(timeout=5)
        return 1
    print(f"[ok] подключено: {json.dumps(reply.get('result'), ensure_ascii=False)}")

    send({"id": "get", "cmd": "get"})
    print(f"[state] {json.dumps(read_reply('get').get('result'), ensure_ascii=False)}")

    send({"id": "info", "cmd": "info"})
    print(f"[info] {json.dumps(read_reply('info').get('result'), ensure_ascii=False)}")

    print(f"[listen] {args.seconds:.0f} с: смените режим даблтапом, снимите/наденьте наушники…")
    deadline = time.monotonic() + args.seconds
    notifications = 0
    while time.monotonic() < deadline:
        try:
            line = lines.get(timeout=max(0.0, deadline - time.monotonic()))
        except queue.Empty:
            break
        if line is None:
            print("[stop] мост закрылся")
            return 1
        if not line.strip():
            continue
        message = json.loads(line)
        if message.get("event") == "notification":
            notifications += 1
            print(f"[notification #{notifications}] id={message.get('id')} payload={message.get('payload')}")
        elif "event" in message:
            print(f"[event] {json.dumps(message, ensure_ascii=False)}")

    print(f"[done] захвачено нотификаций: {notifications}")
    send({"id": "bye", "cmd": "close"})
    try:
        read_reply("bye", timeout=10)
    except Exception:
        pass
    process.stdin.close()
    process.wait(timeout=5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
