#!/usr/bin/env python3
"""
Momentum 4 — GAIA controle (Windows / macOS).

Транспорт: Momentum 4 использует GAIA3 по CLASSIC Bluetooth SPP (RFCOMM),
НЕ BLE. См. bundle.js: GAIA3_SPP_UUID = a2129ff3-081b-4c45-8afe-469d9c4842ec.

SPP-кадр GAIA3 (из SPPGaiaFramer):
  FF <version=3> <flags=0> <len> <GAIA-header+payload>
    len = payload_length (4 байта GAIA-заголовка не входят)
    version=3, flags=0 (без checksum / расширения длины)

GAIA-заголовок: [uint16 vendor BE=0x0495][uint16 cmd BE][payload...]

Транспорт выбирается по ОС (см. gaia_transport.py):
  Windows: winrt StreamSocket + DataWriter/DataReader
  macOS:   IOBluetooth (PyObjC)
"""

import argparse
import asyncio
import platform
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from gaia_transport import create_transport, list_paired_devices

VENDOR = 0x0495

ANC_MODES = {0: "OFF", 1: "ANTI_WIND", 2: "COMFORT", 3: "ADAPTIVE"}
MODE_NAMES = {v.lower(): k for k, v in ANC_MODES.items()}


# ---------- GAIA3 over SPP framing ----------

def gaia_frame(cmd: int, payload: bytes = b"") -> bytes:
    """GAIA-заголовок + payload."""
    return (
        bytes([(VENDOR >> 8) & 0xFF, VENDOR & 0xFF])
        + bytes([(cmd >> 8) & 0xFF, cmd & 0xFF])
        + payload
    )


def hexd(b: bytes) -> str:
    return b.hex(" ")


# ---------- команды ----------

def parse_gaia_rsp(frame: bytes):
    """Разбирает SPP-кадр ответа GAIA: FF 03 00 len | vendor(2) cmd(2) payload."""
    if len(frame) < 8 or frame[0] != 0xFF:
        return None
    vendor = (frame[4] << 8) | frame[5]
    cmd = (frame[6] << 8) | frame[7]
    payload = frame[8:]
    return {"vendor": vendor, "cmd": cmd, "payload": payload}


async def run_command(args):
    bt_addr = args.addr

    if args.cmd == "list":
        devs = list_paired_devices()
        if not devs:
            print("Сопряжённых Bluetooth-устройств не найдено.")
        for d in devs:
            addr = d.get("address") or "?"
            name = d.get("name") or "(без имени)"
            print(f"{addr}  {name}")
        return

    if not bt_addr:
        print("Нужен --addr (Bluetooth-адрес наушников). Сначала выполни: m4_ctl.py list")
        sys.exit(1)

    tr = create_transport(bt_addr)
    await tr.connect()
    try:
        if args.cmd == "anc":
            val = 1 if args.state == "on" else 0
            await tr.send(gaia_frame(0x1A04, bytes([val])))
            print("ANC:", "ON" if val else "OFF", "(отправлено)")

        elif args.cmd == "mode":
            mode = MODE_NAMES[args.mode]
            if mode == 0:
                await tr.send(gaia_frame(0x1A04, bytes([0])))
            else:
                await tr.send(gaia_frame(0x1A00, bytes([mode, 1])))
            print(f"Режим: {ANC_MODES[mode]}")

        elif args.cmd == "custom":
            # Кастом-режим M4: прозрачность выкл -> ANC вкл -> выход из Adaptive -> слайдер ANC 100
            steps = [
                ("0x1804 [0] прозрачность off", gaia_frame(0x1804, bytes([0]))),
                ("0x1A04 [1] ANC on", gaia_frame(0x1A04, bytes([1]))),
                ("0x1A00 [3,0] adaptive off", gaia_frame(0x1A00, bytes([3, 0]))),
                ("0x1A02 [0] слайдер ANC 100", gaia_frame(0x1A02, bytes([0]))),
            ]
            for i, (label, frame) in enumerate(steps):
                if i > 0:
                    # Наушник закрывает SPP после ответа — переподключаемся.
                    await tr.close()
                    tr = create_transport(bt_addr)
                    await tr.connect()
                await tr.send(frame)
                print(f"  {label} -> {frame.hex(' ')}")
            print("Режим: CUSTOM")

        elif args.cmd == "antiwind":
            # Anti-Wind: 0=off, 1=MAX, 2=AUTO
            level = max(0, min(2, args.level))
            await tr.send(gaia_frame(0x1A00, bytes([1, level])))
            print(f"Anti-Wind: {level} (0=off, 1=MAX, 2=AUTO)")

        elif args.cmd == "transparency":
            level = max(0, min(100, args.level))
            # Слайдер Кастома работает только вне Адаптив-режима.
            await tr.send(gaia_frame(0x1A00, bytes([3, 0])))
            await asyncio.sleep(0.3)
            # Наушник закрывает SPP после ответа — переподключаемся.
            await tr.close()
            tr = create_transport(bt_addr)
            await tr.connect()
            await tr.send(gaia_frame(0x1A02, bytes([level])))
            print(f"Прозрачность: {level}/100 (Кастом)")

        elif args.cmd == "get":
            for name, cmd in [
                ("ANC вкл/выкл", 0x1A05),
                ("Режим ANC", 0x1A01),
                ("Прозрачность", 0x1A03),
                ("TransparentHearing", 0x1805),
            ]:
                await tr.send(gaia_frame(cmd))
                f = await tr.recv_frame(timeout=2.0)
                if not f:
                    print(f"{name}: (нет ответа)")
                else:
                    r = parse_gaia_rsp(f)
                    if r:
                        print(f"{name}: cmd=0x{r['cmd']:04x} payload={r['payload'].hex(' ')}")
                    else:
                        print(f"{name}: (не кадр) {f.hex(' ')}")
                # Наушник закрывает SPP после ответа — переподключаемся
                await tr.close()
                tr = create_transport(bt_addr)
                await tr.connect()

        # после команды подождать немного ответов
        await asyncio.sleep(1.0)
        await tr.recv(timeout=2.0)
    finally:
        # На macOS при завершении процесса соединение рвётся. Держим процесс живым,
        # чтобы наушники остались подключёнными к системе (как на Windows).
        if platform.system() == "Darwin":
            try:
                input("Наушники подключены. Нажмите Enter, чтобы отключить и выйти...")
            except EOFError:
                pass
        await tr.close()


async def main():
    ap = argparse.ArgumentParser(description="Momentum 4 — GAIA3 over SPP (Classic BT)")
    ap.add_argument("--addr", help="Bluetooth-адрес наушников (напр. AA:BB:CC:DD:EE:FF)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="список сопряжённых Bluetooth-устройств")
    p = sub.add_parser("anc", help="вкл/выкл шумодав")
    p.add_argument("state", choices=["on", "off"])
    p = sub.add_parser("mode", help="режим ANC")
    p.add_argument("mode", choices=sorted(MODE_NAMES))
    p = sub.add_parser("custom", help="Кастом-режим M4 (Anti-Wind mode, state=0)")
    p = sub.add_parser("antiwind", help="Anti-Wind уровень: 0=off, 1=MAX, 2=AUTO")
    p.add_argument("level", type=int)
    p = sub.add_parser("transparency", aliases=["trans"], help="прозрачность 0..100")
    p.add_argument("level", type=int)
    p = sub.add_parser("get", help="прочитать состояние")

    a = ap.parse_args()
    await run_command(a)


if __name__ == "__main__":
    asyncio.run(main())