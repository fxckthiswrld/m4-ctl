#!/usr/bin/env python3
"""Momentum 4 — постоянный мост для Electron UI.

Протокол: JSON lines по stdin/stdout.
Вход:   {"cmd": "list"|"connect"|"anc"|"mode"|"custom"|"antiwind"|"transparency"|"get"|"close", ...args}
Выход:  {"ok": true, "result": {...}}  или  {"ok": false, "error": "..."}

Мост держит одно SPP-соединение открытым (keepalive) всё время работы аппы.
Если наушник закрыл канал после ответа, следующая команда авто-пересоздаёт
транспорт с ограниченным числом попыток.
"""

import asyncio
import json
import re
import sys
import threading
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from gaia_transport import create_transport, list_paired_devices

VENDOR = 0x0495
COMMAND_TIMEOUT = 60.0

ANC_MODES = {0: "OFF", 1: "ANTI_WIND", 2: "COMFORT", 3: "ADAPTIVE"}
MODE_NAMES = {v.lower(): k for k, v in ANC_MODES.items()}
BT_ADDRESS_RE = re.compile(r"^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$", re.IGNORECASE)


def normalize_bt_address(addr: str) -> str:
    """Validate and normalize a Bluetooth MAC address for transport APIs."""
    if not isinstance(addr, str):
        raise ValueError("Bluetooth-адрес должен быть строкой")
    normalized = addr.strip().replace("-", ":").upper()
    if not BT_ADDRESS_RE.fullmatch(normalized):
        raise ValueError("некорректный Bluetooth-адрес (ожидается AA:BB:CC:DD:EE:FF)")
    return normalized


def gaia_frame(cmd: int, payload: bytes = b"") -> bytes:
    """GAIA-заголовок + payload."""
    return (
        bytes([(VENDOR >> 8) & 0xFF, VENDOR & 0xFF])
        + bytes([(cmd >> 8) & 0xFF, cmd & 0xFF])
        + payload
    )


def parse_gaia_rsp(frame: bytes):
    if len(frame) < 8 or frame[:3] != b"\xff\x03\x00" or len(frame) != 8 + frame[3]:
        return None
    return {
        "vendor": (frame[4] << 8) | frame[5],
        "cmd": (frame[6] << 8) | frame[7],
        "payload": frame[8:],
    }


def parse_state_value(name: str, response):
    """Decode a getter response while retaining its raw payload for diagnostics."""
    payload = response["payload"]
    state = {"raw": payload.hex(" "), "cmd": response["cmd"]}
    if name == "mode":
        # M4 reports three (feature, value) pairs, not a single active-mode byte.
        if len(payload) != 6:
            raise ValueError("invalid ANC mode response length")
        modes = dict(zip(payload[::2], payload[1::2]))
        if set(modes) != {1, 2, 3} or modes[1] not in (0, 1, 2) or any(modes[k] not in (0, 1) for k in (2, 3)):
            raise ValueError("invalid ANC mode response")
        key = "adaptive" if modes[3] else "comfort" if modes[2] else "custom"
        state.update(key=key, name=key.upper(), antiwind=modes[1])
        return state
    if len(payload) != 1:
        raise ValueError(f"invalid {name} response length")
    value = payload[0]
    if name == "anc" or name == "transparent_hearing":
        if value not in (0, 1):
            raise ValueError(f"invalid {name} value")
        state["enabled"] = bool(value)
    elif name == "transparency":
        if value > 100:
            raise ValueError("invalid transparency value")
        state["level"] = value
    else:
        state["value"] = value
    return state


class Bridge:
    def __init__(self, on_event=None):
        self.tr = None
        self.addr = None
        self._command_lock = asyncio.Lock()
        self.on_event = on_event or (lambda event: None)

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
        self.on_event({"event": "reconnecting"})
        for attempt in range(3):
            tr = create_transport(self.addr)
            try:
                await asyncio.wait_for(tr.connect(), 15.0)
                self.tr = tr
                return
            except asyncio.CancelledError:
                await tr.close()
                raise
            except Exception as e:
                last_error = e
                try:
                    await tr.close()
                except Exception:
                    pass
                if attempt < 2:
                    await asyncio.sleep(0.75 * (attempt + 1))
        raise RuntimeError(f"не удалось подключиться к {self.addr}: {last_error}")

    async def _gaia(self, frame: bytes, retry: bool = True, response_timeout: float = 1.2):
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

        command = int.from_bytes(frame[2:4], "big")
        deadline = asyncio.get_running_loop().time() + response_timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                response = await self.tr.recv_frame(timeout=remaining)
            except Exception:
                await self.cmd_close()
                raise
            if not response:
                break
            parsed = parse_gaia_rsp(response)
            if not parsed or parsed["vendor"] != VENDOR:
                continue
            error_ids = {command | 0x0180}
            # The M4 command description also lists these transparency error IDs.
            if command in (0x1A02, 0x1A03):
                error_ids.add(command - 0x80)
            if parsed["cmd"] in error_ids:
                raise RuntimeError(f"GAIA 0x{command:04X} rejected: {parsed['payload'].hex(' ')}")
            if parsed["cmd"] == command | 0x0100:
                return response
        # Discard this channel so a late reply cannot acknowledge a later command.
        await self.cmd_close()
        raise TimeoutError(f"GAIA 0x{command:04X}: no acknowledgement")

    async def cmd_connect(self, addr: str):
        self.addr = normalize_bt_address(addr)
        await self._reconnect()
        return {"connected": True, "addr": self.addr}

    async def cmd_anc(self, state: str):
        if state not in ("on", "off"):
            raise ValueError("invalid ANC state")
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
            if m in (2, 3):
                await self._gaia(gaia_frame(0x1A00, bytes([5 - m, 0])))
            await self._gaia(gaia_frame(0x1A00, bytes([m, 1])))
        return {"mode": ANC_MODES[m]}

    async def cmd_custom(self):
        # Кастом-режим M4: прозрачность off -> ANC on -> выход из Adaptive -> слайдер ANC 100
        steps = [
            gaia_frame(0x1804, bytes([0])),
            gaia_frame(0x1A04, bytes([1])),
            gaia_frame(0x1A00, bytes([3, 0])),
            gaia_frame(0x1A00, bytes([2, 0])),
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
        errors = {}
        for name, cmd in [
            ("anc", 0x1A05),
            ("mode", 0x1A01),
            ("transparency", 0x1A03),
            ("transparent_hearing", 0x1805),
        ]:
            if self.tr is None:
                state[name] = None
                errors[name] = "SPP connection lost; remaining state reads skipped"
                continue
            try:
                f = await self._gaia(gaia_frame(cmd))
                state[name] = parse_state_value(name, parse_gaia_rsp(f))
            except Exception as error:
                state[name] = None
                errors[name] = str(error)
        if not any(value is not None for value in state.values()):
            raise RuntimeError("device state unavailable: " + "; ".join(errors.values()))
        return {"state": state, "errors": errors}

    async def cmd_close(self):
        if self.tr is not None:
            try:
                await self.tr.close()
            except Exception:
                pass
            self.tr = None
        return {"closed": True}

    async def cmd_profile(self, mode, antiwind, transparency):
        if mode not in ("adaptive", "custom", "off"):
            raise ValueError("invalid profile mode")
        if type(antiwind) is not int or antiwind not in (0, 1, 2):
            raise ValueError("invalid profile antiwind")
        if type(transparency) is not int or not 0 <= transparency <= 100:
            raise ValueError("invalid profile transparency")
        if mode == "custom":
            await self.cmd_custom()
            await self.cmd_antiwind(antiwind)
            await self.cmd_transparency(transparency)
        else:
            await self.cmd_mode(mode)
        result = await self.cmd_get()
        state = result["state"]
        anc = (state.get("anc") or {}).get("enabled")
        actual_mode = state.get("mode") or {}
        confirmed = anc is False if mode == "off" else anc is True and actual_mode.get("key") == mode
        if mode == "custom":
            confirmed = confirmed and actual_mode.get("antiwind") == antiwind and (state.get("transparency") or {}).get("level") == transparency
        if not confirmed:
            raise RuntimeError("profile settings were not confirmed by the device")
        return result

    async def cmd_info(self):
        if self.tr is None:
            raise RuntimeError("не подключено")
        result = {"battery": None, "firmware": None, "errors": {}}
        for name, command in [("battery", 0x0603), ("firmware", 0x1202)]:
            if self.tr is None:
                result["errors"][name] = "SPP connection lost; remaining info reads skipped"
                continue
            try:
                frame = await self._gaia(gaia_frame(command), response_timeout=9.0)
                payload = parse_gaia_rsp(frame)["payload"]
                if name == "battery":
                    if not 1 <= len(payload) <= 3 or any(value > 100 and value != 255 for value in payload):
                        raise ValueError("invalid battery response")
                    result[name] = [value for value in payload if value <= 100] or None
                else:
                    if len(payload) not in (3, 6):
                        raise ValueError("invalid firmware response")
                    result[name] = ".".join(str(value) for value in payload)
            except Exception as error:
                result["errors"][name] = str(error)
        return result


HANDLERS = {
    "list": lambda b: list_paired_devices(),
    "connect": lambda b, **k: None,  # async, см. dispatch
}


async def dispatch(bridge: Bridge, msg: dict):
    cmd = msg.get("cmd")
    if cmd == "list":
        return await asyncio.to_thread(list_paired_devices)
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
    if cmd == "info":
        return await bridge.cmd_info()
    if cmd == "profile":
        return await bridge.cmd_profile(msg.get("mode"), msg.get("antiwind"), msg.get("transparency"))
    if cmd == "close":
        return await bridge.cmd_close()
    raise ValueError(f"неизвестная команда: {cmd}")


async def execute_request(bridge, msg):
    remaining = min(COMMAND_TIMEOUT, (msg.get("deadline_ms", time.time() * 1000 + COMMAND_TIMEOUT * 1000) / 1000) - time.time())
    if remaining <= 0:
        raise TimeoutError("command expired before execution")
    try:
        return await asyncio.wait_for(dispatch(bridge, msg), remaining)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        await bridge.cmd_close()
        raise


async def main():
    output = sys.stdout
    # Transport diagnostics must never share the JSON Lines output stream.
    sys.stdout = sys.stderr

    def emit(message):
        output.write(json.dumps(message, ensure_ascii=False) + "\n")
        output.flush()

    bridge = Bridge(emit)
    q = asyncio.Queue()
    loop = asyncio.get_running_loop()
    emit({"event": "ready"})

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
                result = await execute_request(bridge, msg)
            reply = {"ok": True, "result": result}
            if request_id is not None:
                reply["id"] = request_id
            emit(reply)
        except Exception as e:
            reply = {"ok": False, "error": str(e) or "command timed out"}
            if request_id is not None:
                reply["id"] = request_id
            emit(reply)

    await bridge.cmd_close()


if __name__ == "__main__":
    asyncio.run(main())
