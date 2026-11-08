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
import logging
import os
import platform
import random
import re
import sys
import threading
import time
from logging.handlers import RotatingFileHandler


def _check_deadline(deadline_ms):
    """Прерывает многошаговую команду, если истёк её дедлайн Electron."""
    if deadline_ms is not None and time.time() * 1000 >= deadline_ms:
        raise TimeoutError("command expired before the next step")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _setup_logging():
    """Логи идут в stderr; опционально в файл (путь задаёт Electron).

    Файл включается переменной окружения M4_BRIDGE_LOG_FILE и ротируется,
    чтобы долгие сессии не занимали диск.
    """
    logger = logging.getLogger("m4bridge")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(formatter)
    logger.addHandler(stream)
    path = os.environ.get("M4_BRIDGE_LOG_FILE")
    if path:
        try:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            file_handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except OSError as error:
            logger.warning("log file %r unavailable: %r", path, error)
    return logger


LOGGER = _setup_logging()

from gaia_transport import create_transport, list_paired_devices

VENDOR = 0x0495
COMMAND_TIMEOUT = 60.0
RESPONSE_TIMEOUT = 1.2
INFO_RESPONSE_TIMEOUT = 9.0
RX_POLL_TIMEOUT = 0.5
CONNECT_TIMEOUT = 15.0
RECONNECT_ATTEMPTS = 3
RECONNECT_BACKOFF = 0.75
# Liveness: silence longer than this triggers a lightweight state read.
WATCHDOG_TICK = 5.0
LIVENESS_IDLE = 25.0
# Real hardware sends notification bursts during which command acks are
# delayed beyond the normal response timeout; a single missed ping must not
# kill a healthy session.
PING_RESPONSE_TIMEOUT = 3.0
LIVENESS_PINGS = 3
LIVENESS_RETRY_DELAY = 2.0
# Background recovery of a self-dropped session: slow retries with jitter,
# giving up after WINDOW seconds so the next command retries fresh.
BACKGROUND_RECONNECT_START = 5.0
BACKGROUND_RECONNECT_MAX = 30.0
BACKGROUND_RECONNECT_WINDOW = 300.0

# Commands whose legacy firmware replies errors as (command - 0x80).
LEGACY_ERROR_COMMANDS = (0x1A02, 0x1A03)
# GAIA v3 notification registration (m4.json Config_RegisterNotification):
# core 0 = generic state, audio curation 2 = ANC mode, power 3 = battery,
# transparent hearing 12. Verified on hardware: audio curation delivers
# notification 0x0482 with the new mode as a single byte.
REGISTER_NOTIFICATION_CMD = 0x0007
REGISTER_NOTIFICATION_FEATURES = (0x00, 0x02, 0x03, 0x12)
# Audio curation mode notification: single byte, same enum as ANC modes.
DEVICE_MODE_NOTIFICATION = 0x0482

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
        # Frames are received by a single pump task; pending maps a GAIA request
        # ID to the future that must be resolved by its reply.
        self._pending = {}
        self._rx_task = None
        self._rx_tr = None
        # Session recovery: reconnect is owned either by a command (fast attempts)
        # or by the background task after a self-dropped session (slow attempts).
        self._recover_lock = asyncio.Lock()
        self._watchdog_task = None
        self._bg_reconnect_task = None
        self._auto_reconnect = False
        self._last_rx = None
        # In-flight requests keyed by their Electron request id (for cancel).
        self._active = {}
        # Every frame received (responses and notifications alike) proves the
        # channel is alive; the watchdog uses the counter as liveness evidence.
        self._rx_frames = 0
        self.on_event = on_event or (lambda event: None)

    # ---------- receive pump ----------

    def _ensure_rx(self):
        """Гарантирует, что pump читает кадры именно текущего транспорта."""
        tr = self.tr
        if tr is None:
            return
        if self._rx_task is None or self._rx_task.done() or self._rx_tr is not tr:
            self._rx_tr = tr
            self._rx_task = asyncio.create_task(self._rx_loop(tr))

    async def _rx_loop(self, tr):
        loop = asyncio.get_running_loop()
        try:
            while self.tr is tr and tr.is_alive():
                frame = await tr.recv_frame(timeout=RX_POLL_TIMEOUT)
                if self.tr is not tr:
                    return
                if not frame:
                    continue
                self._last_rx = loop.time()
                self._rx_frames += 1
                parsed = parse_gaia_rsp(frame)
                if not parsed or parsed["vendor"] != VENDOR:
                    continue
                if not self._route_frame(frame, parsed):
                    # Unsolicited frame: notification (| 0x0080) or stray reply.
                    if parsed["cmd"] & 0x0080:
                        self.on_event({
                            "event": "notification",
                            "id": f"0x{parsed['cmd']:04X}",
                            "payload": parsed["payload"].hex(" "),
                        })
                        if parsed["cmd"] == DEVICE_MODE_NOTIFICATION and len(parsed["payload"]) == 1 \
                                and parsed["payload"][0] in ANC_MODES:
                            # Verified on hardware: the device pushes the new
                            # ANC mode byte when a gesture changes it.
                            self.on_event({"event": "device-mode", "mode": ANC_MODES[parsed["payload"][0]].lower()})
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Never hide pump failures: they explain spontaneous disconnects.
            LOGGER.warning("SPP receive pump stopped: %r", error)
        finally:
            if self.tr is tr:
                # The channel died on its own: discard it and fail in-flight commands.
                self.tr = None
                self._rx_task = None
                self._rx_tr = None
                self._fail_pending(ConnectionError("SPP connection lost"))
                self.on_event({"event": "connection-lost"})
                try:
                    await tr.close()
                except Exception:
                    pass
                if self._auto_reconnect:
                    self._start_background_reconnect()

    def _route_frame(self, frame: bytes, parsed) -> bool:
        """Резолвит future ожидающей команды; True если кадр потреблён."""
        cmd = parsed["cmd"]
        for request_id, fut in self._pending.items():
            candidates = {request_id | 0x0100, request_id | 0x0180}
            if request_id in LEGACY_ERROR_COMMANDS:
                candidates.add(request_id - 0x80)
            if cmd in candidates:
                if not fut.done():
                    fut.set_result(frame)
                return True
        return False

    def _fail_pending(self, error):
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(error)
        self._pending.clear()

    @staticmethod
    def _drain(fut):
        """Помечает исключение future полученным, чтобы не было предупреждений GC."""
        if fut.done():
            try:
                fut.exception()
            except asyncio.CancelledError:
                pass

    async def _teardown(self):
        """Закрывает текущий канал: гасит pump, фейлит pending, закрывает транспорт."""
        tr, self.tr = self.tr, None
        rx, self._rx_task = self._rx_task, None
        self._rx_tr = None
        if rx is not None:
            rx.cancel()
        self._fail_pending(ConnectionError("SPP connection lost"))
        if tr is not None:
            try:
                await tr.close()
            except Exception:
                pass

    # ---------- session recovery ----------

    def _ensure_watchdog(self):
        if self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = asyncio.create_task(self._watchdog())

    def _start_background_reconnect(self):
        # Если текущая задача отменяется (teardown теста/приложения), новая
        # фоновая задача уцелеет после сняка отмен и будет мешать закрытию.
        current = asyncio.current_task()
        if current is not None and getattr(current, "cancelling", lambda: 0)():
            LOGGER.info("background reconnect skipped: owner task is cancelling")
            return
        if self._bg_reconnect_task is None or self._bg_reconnect_task.done():
            self._bg_reconnect_task = asyncio.create_task(self._background_reconnect())

    async def _register_notifications(self):
        """Подписывается на события устройства (зарегистрированная подписка).

        GAIA v3: команда 0x0007 с featureID; ответ 0x0107. После подписки
        устройство присылает изменения состояния (батарея, ANC) с битом 0080,
        которые pump пересылает в UI как события notification. Неудача
        подписки не разрывает сессию: не каждое firmware поддерживает все
        фичи.
        """
        for feature in REGISTER_NOTIFICATION_FEATURES:
            try:
                await self._gaia(gaia_frame(REGISTER_NOTIFICATION_CMD, bytes([feature])))
                LOGGER.info("notification subscription ok: feature %d", feature)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                LOGGER.warning("notification subscription failed for feature %d: %r", feature, error)

    async def _reconnect_now(self):
        """Быстрые попытки восстановления (вызывать под _recover_lock)."""
        if not self.addr:
            raise RuntimeError("не выбран Bluetooth-адрес")
        await self._teardown()
        last_error = None
        self.on_event({"event": "reconnecting"})
        for attempt in range(RECONNECT_ATTEMPTS):
            tr = create_transport(self.addr)
            try:
                await asyncio.wait_for(tr.connect(), CONNECT_TIMEOUT)
                self.tr = tr
                self._last_rx = asyncio.get_running_loop().time()
                self._auto_reconnect = True
                self._ensure_rx()
                self._ensure_watchdog()
                self.on_event({"event": "reconnected"})
                await self._register_notifications()
                return
            except asyncio.CancelledError:
                try:
                    await tr.close()
                except Exception:
                    pass
                raise
            except Exception as e:
                last_error = e
                try:
                    await tr.close()
                except Exception:
                    pass
                if attempt < RECONNECT_ATTEMPTS - 1:
                    await asyncio.sleep(RECONNECT_BACKOFF * (attempt + 1))
        raise RuntimeError(f"не удалось подключиться к {self.addr}: {last_error}")

    async def _recover(self):
        """Возвращает управление с живым каналом; быстрые попытки при обрыве."""
        async with self._recover_lock:
            if self.tr is not None and self.tr.is_alive():
                return
            await self._reconnect_now()

    async def _background_reconnect(self):
        """Медленное фоновое восстановление самопроизвольно потерянной сессии."""
        loop = asyncio.get_running_loop()
        started = loop.time()
        delay = BACKGROUND_RECONNECT_START
        while self.tr is None and self._auto_reconnect:
            self.on_event({"event": "reconnecting", "background": True})
            async with self._recover_lock:
                if self.tr is None and self._auto_reconnect:
                    try:
                        tr = create_transport(self.addr)
                        try:
                            await asyncio.wait_for(tr.connect(), CONNECT_TIMEOUT)
                        except Exception:
                            try:
                                await tr.close()
                            except Exception:
                                pass
                            raise
                        self.tr = tr
                        self._last_rx = loop.time()
                        self._ensure_rx()
                        self.on_event({"event": "reconnected"})
                        await self._register_notifications()
                        return
                    except asyncio.CancelledError:
                        raise
                    except Exception as error:
                        LOGGER.info("background reconnect failed: %r", error)
            if loop.time() - started >= BACKGROUND_RECONNECT_WINDOW:
                LOGGER.warning("background reconnect gave up")
                self._auto_reconnect = False
                self.on_event({"event": "unreachable"})
                return
            await asyncio.sleep(delay + random.uniform(0, delay * 0.3))
            delay = min(delay * 1.5, BACKGROUND_RECONNECT_MAX)

    async def _ping(self):
        """Лёгкое чтение состояния как проверка живости канала."""
        await self._gaia(gaia_frame(0x1A05), response_timeout=PING_RESPONSE_TIMEOUT)

    async def _watchdog(self):
        """Замечает молчащий мёртвый канал: лёгкий ping с повторами, затем сброс.

        Реальное устройство после пачки нотификаций может задержать ack дольше
        обычного таймаута. Поэтому: три ping'а по 3 с, и если во время
        неудачного ping'а пришёл любой кадр — канал жив, сброс не делаем.
        """
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(WATCHDOG_TICK)
            tr = self.tr
            if tr is None or not tr.is_alive():
                continue
            if self._last_rx is None:
                self._last_rx = loop.time()
                continue
            if loop.time() - self._last_rx < LIVENESS_IDLE:
                continue
            dropped = True
            for attempt in range(LIVENESS_PINGS):
                frames_before = self._rx_frames
                try:
                    # Any reply proves the channel is alive; ANC state is the cheapest read.
                    async with self._command_lock:
                        await self._ping()
                    dropped = False
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    LOGGER.warning("liveness ping %d/%d failed: %r", attempt + 1, LIVENESS_PINGS, error)
                    if self._rx_frames != frames_before:
                        # Frames arrived while the ping was failing: the device
                        # is alive, only slow to answer commands.
                        LOGGER.info("frames arrived during failed ping; keeping the channel")
                        dropped = False
                        break
                    if attempt < LIVENESS_PINGS - 1:
                        await asyncio.sleep(LIVENESS_RETRY_DELAY)
            if dropped:
                await self._teardown()
                if self._auto_reconnect:
                    self._start_background_reconnect()

    async def _gaia(self, frame: bytes, response_timeout: float = None):
        """Отправляет команду и дожидается ответа через receive pump.

        Таймаут ответа НЕ закрывает канал: pump остаётся жить и просто
        отбрасывает запоздавший ответ, поэтому он не может ошибочно
        подтвердить последующую команду.
        """
        if response_timeout is None:
            response_timeout = RESPONSE_TIMEOUT
        if self.tr is None or not self.tr.is_alive():
            await self._recover()
        self._ensure_rx()
        command = int.from_bytes(frame[2:4], "big")
        fut = asyncio.get_running_loop().create_future()
        self._pending[command] = fut
        try:
            try:
                await self.tr.send(frame)
            except Exception:
                await self._recover()
                # Recovery failed the in-flight future; register a fresh one.
                self._drain(fut)
                fut = asyncio.get_running_loop().create_future()
                self._pending[command] = fut
                await self.tr.send(frame)
            response = await asyncio.wait_for(fut, response_timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(f"GAIA 0x{command:04X}: no acknowledgement") from None
        finally:
            if self._pending.get(command) is fut:
                del self._pending[command]
            self._drain(fut)
        parsed = parse_gaia_rsp(response)
        error_ids = {command | 0x0180}
        if command in LEGACY_ERROR_COMMANDS:
            error_ids.add(command - 0x80)
        if parsed is not None and parsed["cmd"] in error_ids:
            raise RuntimeError(f"GAIA 0x{command:04X} rejected: {parsed['payload'].hex(' ')}")
        return response

    async def cmd_connect(self, addr: str):
        self.addr = normalize_bt_address(addr)
        async with self._recover_lock:
            await self._reconnect_now()
        return {"connected": True, "addr": self.addr}

    async def cmd_anc(self, state: str):
        if state not in ("on", "off"):
            raise ValueError("invalid ANC state")
        val = 1 if state == "on" else 0
        await self._gaia(gaia_frame(0x1A04, bytes([val])))
        return {"anc": "ON" if val else "OFF"}

    async def cmd_mode(self, mode: str, deadline_ms=None):
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
            _check_deadline(deadline_ms)
            await self._gaia(gaia_frame(0x1A04, bytes([1])))
            await asyncio.sleep(0.4)
            _check_deadline(deadline_ms)
            if m in (2, 3):
                await self._gaia(gaia_frame(0x1A00, bytes([5 - m, 0])))
            await self._gaia(gaia_frame(0x1A00, bytes([m, 1])))
        return {"mode": ANC_MODES[m]}

    async def cmd_custom(self, deadline_ms=None):
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
                _check_deadline(deadline_ms)
        return {"mode": "CUSTOM"}

    async def cmd_antiwind(self, level: int):
        level = max(0, min(2, int(level)))
        await self._gaia(gaia_frame(0x1A00, bytes([1, level])))
        return {"antiwind": level}

    async def cmd_transparency(self, level: int, deadline_ms=None):
        level = max(0, min(100, int(level)))
        # Слайдер Кастома работает только вне Адаптив-режима.
        await self._gaia(gaia_frame(0x1A00, bytes([3, 0])))
        await asyncio.sleep(0.4)
        _check_deadline(deadline_ms)
        await self._gaia(gaia_frame(0x1A02, bytes([level])))
        return {"transparency": level}

    async def cmd_get(self):
        if self.tr is None:
            raise RuntimeError("не подключено")
        state = {}
        errors = {}
        fields = [
            ("anc", 0x1A05),
            ("mode", 0x1A01),
            ("transparency", 0x1A03),
            ("transparent_hearing", 0x1805),
        ]
        for index, (name, cmd) in enumerate(fields):
            if self.tr is None:
                state[name] = None
                errors[name] = "SPP connection lost; remaining state reads skipped"
                continue
            try:
                f = await self._gaia(gaia_frame(cmd))
                state[name] = parse_state_value(name, parse_gaia_rsp(f))
            except (TimeoutError, OSError) as error:
                # The channel is suspect: stop this batch, the pump keeps the
                # socket open and the next command recovers transparently.
                state[name] = None
                errors[name] = str(error)
                for other, _ in fields[index + 1:]:
                    state[other] = None
                    errors[other] = "SPP connection lost; remaining state reads skipped"
                break
            except Exception as error:
                state[name] = None
                errors[name] = str(error)
        if not any(value is not None for value in state.values()):
            raise RuntimeError("device state unavailable: " + "; ".join(errors.values()))
        return {"state": state, "errors": errors}

    async def cmd_close(self):
        # An explicit close is user intent: no background recovery afterwards.
        self._auto_reconnect = False
        task, self._bg_reconnect_task = self._bg_reconnect_task, None
        if task is not None:
            task.cancel()
        await self._teardown()
        LOGGER.info("session closed by request")
        return {"closed": True}

    async def aclose(self, timeout: float = 2.0):
        """Полностью гасит мост и все его задачи (используется тестами).

        Если какая-то задача не завершилась после cancel, печатает её стек —
        так находится виновник зависаний teardown.
        """
        try:
            await self.cmd_close()
        except Exception as error:
            LOGGER.warning("close during aclose failed: %r", error)
        tasks = [t for t in (self._watchdog_task, self._bg_reconnect_task, self._rx_task)
                 if t is not None and not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=timeout)
            for task in pending:
                LOGGER.error("task %r survived cancel; stack: %r", task, task.get_stack(limit=5))

    async def cmd_cancel(self, target=None):
        """Отменяет активные команды: конкретную по target или все."""
        if target is not None:
            task = self._active.get(target)
            if task is None:
                return {"cancelled": []}
            task.cancel()
            return {"cancelled": [target]}
        cancelled = sorted(str(key) for key in self._active)
        for task in self._active.values():
            task.cancel()
        return {"cancelled": cancelled}

    async def cmd_profile(self, mode, antiwind, transparency, deadline_ms=None):
        if mode not in ("adaptive", "custom", "off"):
            raise ValueError("invalid profile mode")
        if type(antiwind) is not int or antiwind not in (0, 1, 2):
            raise ValueError("invalid profile antiwind")
        if type(transparency) is not int or not 0 <= transparency <= 100:
            raise ValueError("invalid profile transparency")
        if mode == "custom":
            await self.cmd_custom(deadline_ms)
            await self.cmd_antiwind(antiwind)
            await self.cmd_transparency(transparency, deadline_ms)
        else:
            await self.cmd_mode(mode, deadline_ms)
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
        fields = [("battery", 0x0603), ("firmware", 0x1202)]
        for index, (name, command) in enumerate(fields):
            if self.tr is None:
                result["errors"][name] = "SPP connection lost; remaining info reads skipped"
                continue
            try:
                frame = await self._gaia(gaia_frame(command), response_timeout=INFO_RESPONSE_TIMEOUT)
                payload = parse_gaia_rsp(frame)["payload"]
                if name == "battery":
                    if not 1 <= len(payload) <= 3 or any(value > 100 and value != 255 for value in payload):
                        raise ValueError("invalid battery response")
                    result[name] = [value for value in payload if value <= 100] or None
                else:
                    if len(payload) not in (3, 6):
                        raise ValueError("invalid firmware response")
                    result[name] = ".".join(str(value) for value in payload)
            except (TimeoutError, OSError) as error:
                result["errors"][name] = str(error)
                for other, _ in fields[index + 1:]:
                    result["errors"][other] = "SPP connection lost; remaining info reads skipped"
                break
            except Exception as error:
                result["errors"][name] = str(error)
        return result


HANDLERS = {
    "list": lambda b: list_paired_devices(),
    "connect": lambda b, **k: None,  # async, см. dispatch
}


async def dispatch(bridge: Bridge, msg: dict, deadline_ms=None):
    cmd = msg.get("cmd")
    if cmd == "list":
        if platform.system() == "Darwin":
            return list_paired_devices()
        return await asyncio.to_thread(list_paired_devices)
    if cmd == "connect":
        return await bridge.cmd_connect(msg.get("addr", ""))
    if cmd == "anc":
        return await bridge.cmd_anc(msg.get("state", "off"))
    if cmd == "mode":
        return await bridge.cmd_mode(msg.get("mode", "adaptive"), deadline_ms)
    if cmd == "custom":
        return await bridge.cmd_custom(deadline_ms)
    if cmd == "antiwind":
        return await bridge.cmd_antiwind(msg.get("level", 0))
    if cmd == "transparency":
        return await bridge.cmd_transparency(msg.get("level", 0), deadline_ms)
    if cmd == "get":
        return await bridge.cmd_get()
    if cmd == "info":
        return await bridge.cmd_info()
    if cmd == "profile":
        return await bridge.cmd_profile(msg.get("mode"), msg.get("antiwind"), msg.get("transparency"), deadline_ms)
    if cmd == "close":
        return await bridge.cmd_close()
    raise ValueError(f"неизвестная команда: {cmd}")


async def execute_request(bridge, msg):
    deadline_ms = msg.get("deadline_ms", time.time() * 1000 + COMMAND_TIMEOUT * 1000)
    remaining = min(COMMAND_TIMEOUT, deadline_ms / 1000 - time.time())
    if remaining <= 0:
        raise TimeoutError("command expired before execution")
    async with bridge._command_lock:
        try:
            return await asyncio.wait_for(dispatch(bridge, msg, deadline_ms), remaining)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            await bridge.cmd_close()
            raise


async def run_request(bridge, emit, request_id, msg):
    """Выполняет один запрос; отменяется через bridge.cmd_cancel.

    Сериализацию команд обеспечивает execute_request (общий command lock).
    """
    task = asyncio.current_task()
    if request_id is not None and task is not None:
        bridge._active[request_id] = task
    try:
        result = await execute_request(bridge, msg)
    except asyncio.CancelledError:
        reply = {"ok": False, "error": "cancelled", "cancelled": True}
    except Exception as e:
        reply = {"ok": False, "error": str(e) or "command timed out"}
    else:
        reply = {"ok": True, "result": result}
    if request_id is not None:
        reply["id"] = request_id
    emit(reply)
    if request_id is not None:
        bridge._active.pop(request_id, None)


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
        try:
            msg = json.loads(line)
            if not isinstance(msg, dict):
                raise ValueError("сообщение должно быть JSON-объектом")
        except Exception as e:
            emit({"ok": False, "error": str(e)})
            continue
        request_id = msg.get("id")
        if msg.get("cmd") == "cancel":
            reply = await bridge.cmd_cancel(msg.get("target"))
            if request_id is not None:
                reply["id"] = request_id
            emit(reply)
            continue
        task = asyncio.create_task(run_request(bridge, emit, request_id, msg))

    # Shutdown: stop in-flight work, then close the channel gracefully.
    for task in list(bridge._active.values()):
        task.cancel()
    await asyncio.gather(*bridge._active.values(), return_exceptions=True)
    await bridge.cmd_close()


if __name__ == "__main__":
    asyncio.run(main())
