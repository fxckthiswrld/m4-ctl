#!/usr/bin/env python3
"""Momentum 4 — постоянный мост для Electron UI.

Протокол: JSON lines по stdin/stdout.
Вход:   {"cmd": "list"|"connect"|"anc"|"mode"|"custom"|"antiwind"|"transparency"|"get"|"close", ...args}
Выход:  {"ok": true, "result": {...}}  или  {"ok": false, "error": "..."}

Мост держит одно SPP-соединение открытым (keepalive) всё время работы аппы.
Если наушник закрыл канал после ответа, следующая команда авто-пересоздаёт
транспорт (на macOS keepalive-поток транспорта переоткрывает канал сам).
"""

import asyncio
import json
import sys
import threading

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from gaia_transport import create_transport, list_paired_devices

VENDOR = 0x0495

ANC_MODES = {0: "OFF", 1: "ANTI_WIND", 2: "COMFORT", 3: "ADAPTIVE"}
MODE_NAMES = {v.lower(): k for k, v in ANC_MODES.items()}


def gaia_frame(cmd: int, payload: bytes = b"") -> bytes:
    """GAIA-заголовок + payload."""
    return (
        bytes([(VENDOR >> 8) & 0xFF, VENDOR & 0xFF])
        + bytes([(cmd >> 8) & 0xFF, cmd & 0xFF])
        + payload
    )


def parse_gaia_rsp(frame: bytes):
    if len(frame) < 8 or frame[0] != 0xFF:
        return None
    return {
        "vendor": (frame[4] << 8) | frame[5],
        "cmd": (frame[6] << 8) | frame[7],
        "payload": frame[8:],
    }


class Bridge:
    def __init__(self):
        self.tr = None
        self.addr = None
        self._command_lock = asyncio.Lock()

    async def _reconnect(self):
        if not self.addr:
            raise RuntimeError("не выбран Bluetooth-адрес")
        if self.tr is not None:
            try:
                await self.tr.close()
            except Exception:
                pass
            self.tr = None
        last_error = None
        for attempt in range(3):
            tr = create_transport(self.addr)
            try:
                await tr.connect()
                self.tr = tr
                return
            except Exception as e:
                last_error = e
                try:
                    await tr.close()
                except Exception:
                    pass
                if attempt < 2:
                    await asyncio.sleep(0.75 * (attempt + 1))
        raise RuntimeError(f"не удалось подключиться к {self.addr}: {last_error}")

    async def _gaia(self, frame: bytes, retry: bool = True):
        """Отправляет команду, забирает ответ и восстанавливает SPP-канал."""
        if self.tr is None or not self.tr.is_alive():
            await self._reconnect()
        try:
            await self.tr.send(frame)
        except Exception:
            if not retry:
                raise
            await self._reconnect()
            await self.tr.send(frame)

        try:
            response = await self.tr.recv_frame(timeout=1.2)
        except Exception as e:
            print(f"[bridge] ответ GAIA не прочитан: {e!r}", file=sys.stderr)
            response = b""

        if not self.tr.is_alive():
            await self._reconnect()
        return response

    async def cmd_connect(self, addr: str):
        self.addr = addr
        await self._reconnect()
        return {"connected": True, "addr": addr}

    async def cmd_anc(self, state: str):
        val = 1 if state == "on" else 0
        await self._gaia(gaia_frame(0x1A04, bytes([val])))
        return {"anc": "ON" if val else "OFF"}

    async def cmd_mode(self, mode: str):
        m = MODE_NAMES.get(mode.lower())
        if m is None:
            raise ValueError(f"неизвестный режим: {mode}")
        if m == 0:
            await self._gaia(gaia_frame(0x1A04, bytes([0])))
        else:
            # Из OFF-состояния (ANC выкл) 0x1A00 [m,1] не срабатывает —
            # сначала прозрачность off, ANC on, потом режим (как в custom).
            await self._gaia(gaia_frame(0x1804, bytes([0])))
            await asyncio.sleep(0.4)
            await self._gaia(gaia_frame(0x1A04, bytes([1])))
            await asyncio.sleep(0.4)
            await self._gaia(gaia_frame(0x1A00, bytes([m, 1])))
        return {"mode": ANC_MODES[m]}

    async def cmd_custom(self):
        # Кастом-режим M4: прозрачность off -> ANC on -> выход из Adaptive -> слайдер ANC 100
        steps = [
            gaia_frame(0x1804, bytes([0])),
            gaia_frame(0x1A04, bytes([1])),
            gaia_frame(0x1A00, bytes([3, 0])),
            gaia_frame(0x1A02, bytes([0])),
        ]
        for i, frame in enumerate(steps):
            await self._gaia(frame)
            if i < len(steps) - 1:
                await asyncio.sleep(0.4)
        return {"mode": "CUSTOM"}

    async def cmd_antiwind(self, level: int):
        level = max(0, min(2, int(level)))
        await self._gaia(gaia_frame(0x1A00, bytes([1, level])))
        return {"antiwind": level}

    async def cmd_transparency(self, level: int):
        level = max(0, min(100, int(level)))
        # Слайдер Кастома работает только вне Адаптив-режима.
        await self._gaia(gaia_frame(0x1A00, bytes([3, 0])))
        await asyncio.sleep(0.4)
        await self._gaia(gaia_frame(0x1A02, bytes([level])))
        return {"transparency": level}

    async def cmd_get(self):
        if self.tr is None:
            raise RuntimeError("не подключено")
        state = {}
        for name, cmd in [
            ("anc", 0x1A05),
            ("mode", 0x1A01),
            ("transparency", 0x1A03),
            ("transparent_hearing", 0x1805),
        ]:
            try:
                f = await self._gaia(gaia_frame(cmd))
            except Exception:
                f = b""
            if not f:
                state[name] = None
            else:
                r = parse_gaia_rsp(f)
                state[name] = r["payload"].hex(" ") if r else None
        return {"state": state}

    async def cmd_close(self):
        if self.tr is not None:
            try:
                await self.tr.close()
            except Exception:
                pass
            self.tr = None
        return {"closed": True}


HANDLERS = {
    "list": lambda b: list_paired_devices(),
    "connect": lambda b, **k: None,  # async, см. dispatch
}


async def dispatch(bridge: Bridge, msg: dict):
    cmd = msg.get("cmd")
    if cmd == "list":
        return list_paired_devices()
    if cmd == "connect":
        return await bridge.cmd_connect(msg.get("addr", ""))
    if cmd == "anc":
        return await bridge.cmd_anc(msg.get("state", "off"))
    if cmd == "mode":
        return await bridge.cmd_mode(msg.get("mode", "adaptive"))
    if cmd == "custom":
        return await bridge.cmd_custom()
    if cmd == "antiwind":
        return await bridge.cmd_antiwind(msg.get("level", 0))
    if cmd == "transparency":
        return await bridge.cmd_transparency(msg.get("level", 0))
    if cmd == "get":
        return await bridge.cmd_get()
    if cmd == "close":
        return await bridge.cmd_close()
    raise ValueError(f"неизвестная команда: {cmd}")


async def main():
    bridge = Bridge()
    q = asyncio.Queue()
    loop = asyncio.get_running_loop()
    sys.stdout.write(json.dumps({"event": "ready"}) + "\n")
    sys.stdout.flush()

    def read_stdin():
        for line in sys.stdin:
            # asyncio.Queue не потокобезопасна — ставим через call_soon_threadsafe
            loop.call_soon_threadsafe(q.put_nowait, line)
        loop.call_soon_threadsafe(q.put_nowait, None)

    threading.Thread(target=read_stdin, daemon=True).start()

    while True:
        line = await q.get()
        if line is None:
            break
        line = line.strip()
        if not line:
            continue
        request_id = None
        try:
            msg = json.loads(line)
            if not isinstance(msg, dict):
                raise ValueError("сообщение должно быть JSON-объектом")
            request_id = msg.get("id")
            async with bridge._command_lock:
                result = await dispatch(bridge, msg)
            reply = {"ok": True, "result": result}
            if request_id is not None:
                reply["id"] = request_id
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except Exception as e:
            reply = {"ok": False, "error": str(e)}
            if request_id is not None:
                reply["id"] = request_id
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()

    await bridge.cmd_close()


if __name__ == "__main__":
    asyncio.run(main())
