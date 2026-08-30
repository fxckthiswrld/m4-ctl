#!/usr/bin/env python3
"""
Транспорт SPP (RFCOMM) GAIA3 для Momentum 4, кроссплатформенный.

- Windows: winrt StreamSocket
- macOS:   IOBluetooth (PyObjC)
"""

import asyncio
import platform
import sys
import threading
import time
import uuid

GAIA3_SPP_UUID = "a2129ff3-081b-4c45-8afe-469d9c4842ec"


def hexd(b: bytes) -> str:
    return b.hex(" ")


def spp_frame(gaia: bytes) -> bytes:
    """Оборачивает GAIA-пакет в SPP-кадр GAIA3 (payload_len = len(gaia) - 4)."""
    payload_len = len(gaia) - 4
    if payload_len < 0 or payload_len > 254:
        raise ValueError("payload too big")
    return bytes([0xFF, 3, 0, payload_len]) + gaia


class BaseSppTransport:
    def __init__(self, bt_addr: str):
        self.bt_addr = bt_addr

    async def connect(self):
        raise NotImplementedError

    async def send(self, gaia: bytes):
        raise NotImplementedError

    async def recv(self, timeout: float = 3.0) -> bytes:
        raise NotImplementedError

    async def recv_frame(self, timeout: float = 3.0) -> bytes:
        raise NotImplementedError

    async def close(self):
        raise NotImplementedError

    def is_alive(self) -> bool:
        return False


def take_spp_frame(buffer: bytearray):
    """Извлекает один SPP-кадр, сохраняя хвост для следующего ответа."""
    while True:
        if len(buffer) < 4:
            return None
        if buffer[0] != 0xFF or buffer[1] != 3:
            marker = buffer.find(b"\xff\x03")
            if marker < 0:
                buffer.clear()
            else:
                del buffer[:marker]
            return b""
        total = 8 + buffer[3]
        if len(buffer) < total:
            return None
        frame = bytes(buffer[:total])
        del buffer[:total]
        return frame


# ---------- Windows: winrt StreamSocket ----------

try:
    from winrt.windows.devices.bluetooth import BluetoothDevice
    from winrt.windows.devices.bluetooth.rfcomm import RfcommServiceId
    from winrt.windows.networking import HostName
    from winrt.windows.networking.sockets import StreamSocket
    from winrt.windows.storage.streams import (
        DataReader,
        DataWriter,
        InputStreamOptions,
    )
    HAS_WINRT = True
except ImportError:
    HAS_WINRT = False


class WinSppTransport(BaseSppTransport):
    def __init__(self, bt_addr: str):
        super().__init__(bt_addr)
        self.sock = None
        self.reader = None
        self.writer = None
        self._rx_queue = asyncio.Queue()
        self._rx_task = None
        self._rx_buffer = bytearray()
        self._closed = False

    async def connect(self):
        if not HAS_WINRT:
            raise RuntimeError("На Windows нужны пакеты winrt: pip install winrt-Windows.Devices.Bluetooth winrt-Windows.Devices.Bluetooth.Rfcomm winrt-Windows.Networking winrt-Windows.Networking.Sockets winrt-Windows.Storage.Streams")

        addr_int = int(self.bt_addr.replace(":", ""), 16)
        dev = BluetoothDevice.from_bluetooth_address_async(addr_int).get()
        if not dev or not dev.name:
            raise RuntimeError(f"Устройство {self.bt_addr} не найдено (проверь сопряжение).")

        sid = RfcommServiceId.from_uuid(uuid.UUID(GAIA3_SPP_UUID))
        res = dev.get_rfcomm_services_for_id_async(sid).get()
        services = list(res.services)
        if not services:
            raise RuntimeError("RFCOMM-сервис GAIA3 не найден на устройстве.")
        print(f"RFCOMM-сервисов GAIA3: {len(services)}")

        svc = services[0]
        host = HostName(svc.connection_host_name.raw_name)

        self.sock = StreamSocket()
        self.sock.connect_async(host, str(svc.connection_service_name)).get()
        print("SPP-соединение установлено.")

        self.reader = DataReader(self.sock.input_stream)
        self.writer = DataWriter(self.sock.output_stream)
        self.reader.input_stream_options = InputStreamOptions.PARTIAL
        self._closed = False
        self._rx_buffer.clear()
        self._rx_task = asyncio.create_task(self._rx_loop())

    async def _rx_loop(self):
        try:
            while not self._closed:
                done = asyncio.Event()

                async def waiter():
                    try:
                        await self.reader.load_async(1)
                    finally:
                        done.set()

                fut = asyncio.ensure_future(waiter())
                try:
                    await asyncio.wait_for(done.wait(), None)
                    if fut.exception() is not None:
                        raise fut.exception()
                except asyncio.CancelledError:
                    fut.cancel()
                    raise
                except Exception as e:
                    print(f"[win] SPP receive stopped: {e!r}")
                    fut.cancel()
                    break
                n = self.reader.unconsumed_buffer_length
                if n == 0:
                    await asyncio.sleep(0.05)
                    continue
                data = bytes(self.reader.read_buffer(n))
                await self._rx_queue.put(data)
        finally:
            if not self._closed:
                print("[win] SPP-канал закрыт")
            self._closed = True

    async def _next_frame(self, timeout: float):
        deadline = time.monotonic() + timeout
        while True:
            frame = take_spp_frame(self._rx_buffer)
            if frame is not None:
                return frame
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return b""
            try:
                chunk = await asyncio.wait_for(self._rx_queue.get(), remaining)
            except Exception:
                return b""
            self._rx_buffer.extend(chunk)

    async def recv(self, timeout: float = 3.0) -> bytes:
        try:
            data = await asyncio.wait_for(self._rx_queue.get(), timeout)
        except asyncio.TimeoutError:
            return b""
        print("RX:", hexd(data))
        return data

    async def recv_frame(self, timeout: float = 3.0) -> bytes:
        frame = await self._next_frame(timeout)
        if frame:
            print("RX:", hexd(frame))
        return frame

    async def send(self, gaia: bytes):
        if self._closed or self.sock is None or self.writer is None:
            raise OSError("SPP-канал не открыт")
        buf = spp_frame(gaia)
        self.writer.write_bytes(buf)
        self.writer.store_async().get()
        print("TX:", hexd(buf))

    def is_alive(self) -> bool:
        return not self._closed and self.sock is not None and self._rx_task is not None and not self._rx_task.done()

    async def close(self):
        self._closed = True
        try:
            if self._rx_task:
                self._rx_task.cancel()
        except Exception:
            pass
        try:
            if self.reader:
                self.reader.close()
        except Exception:
            pass
        try:
            if self.writer:
                self.writer.close()
        except Exception:
            pass
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass


# ---------- macOS: IOBluetooth (PyObjC) ----------

try:
    from Foundation import NSObject, NSRunLoop, NSDate
    from IOBluetooth import IOBluetoothDevice, IOBluetoothSDPUUID
    HAS_IOBT = True
except ImportError:
    HAS_IOBT = False


class MacSppTransport(BaseSppTransport):
    """RFCOMM-канал через IOBluetooth.

    IOBluetooth требует run-loop: подключаемся и читаем в фоновом потоке,
    данные приходят в delegate-колбэках -> asyncio-очередь.
    """

    def __init__(self, bt_addr: str):
        super().__init__(bt_addr)
        self._loop = None
        self._q = None
        self._thread = None
        self._device = None
        self._channel = None
        self._delegate = None
        self._closed = False
        self._opened = threading.Event()
        self._channel_id = None
        self._open_done = None
        self._open_status = 0
        self._rx_buffer = bytearray()

    async def connect(self):
        if not HAS_IOBT:
            raise RuntimeError("На macOS нужен PyObjC IOBluetooth: pip install pyobjc-framework-IOBluetooth")
        self._loop = asyncio.get_running_loop()
        self._q = asyncio.Queue()
        self._closed = False
        self._rx_buffer.clear()
        self._thread = threading.Thread(target=self._thread_main, daemon=True)
        self._thread.start()
        if not await asyncio.to_thread(self._opened.wait, 15.0):
            raise RuntimeError("Не удалось открыть RFCOMM-канал GAIA3 на macOS.")
        if self._channel is None:
            raise RuntimeError("Не удалось открыть RFCOMM-канал GAIA3 на macOS.")
        print("SPP-соединение установлено.")

    def _make_delegate(self):
        """Создаёт свежий делегат, который шлёт данные в asyncio-очередь."""
        import Foundation
        queue = self._q
        loop = self._loop
        transport = self

        class Delegate(Foundation.NSObject):
            def rfcommChannelOpenComplete_status_(self, channel, status):
                transport._open_status = status
                if status != 0:
                    print(f"[mac] openComplete status={status:#x}")
                if transport._open_done is not None:
                    transport._open_done.set()

            def rfcommChannelData_data_length_(self, channel, data, length):
                try:
                    raw = bytes(data)[:length]
                except Exception:
                    raw = b""
                loop.call_soon_threadsafe(queue.put_nowait, raw)

            def rfcommChannelClosed_(self, channel):
                print("[mac] канал закрыт")
                transport._channel = None
                loop.call_soon_threadsafe(queue.put_nowait, b"")

        self._delegate = Delegate.alloc().init()
        return self._delegate

    def _thread_main(self):
        import Foundation
        from IOBluetooth import IOBluetoothDevice, IOBluetoothSDPUUID

        queue = self._q
        loop = self._loop
        opened = self._opened
        transport = self

        self._open_done = threading.Event()

        try:
            # IOBluetooth ожидает формат с дефисами: 80-C3-BA-9C-A5-4F
            dev_addr = self.bt_addr.replace(":", "-")
            print(f"[mac] ищем устройство {dev_addr}")
            dev = IOBluetoothDevice.deviceWithAddressString_(dev_addr)
            if dev is None:
                print("[mac] устройство не найдено по адресу")
                opened.set()
                return
            self._device = dev
            print(f"[mac] найдено: {_mac_attr(dev, 'name')}")
            status = dev.openConnection()
            print(f"[mac] openConnection -> {status:#x}")
            if status != 0:
                opened.set()
                return

            sdp_uuid = _mac_sdp_uuid(GAIA3_SPP_UUID)
            channel_id = None
            if sdp_uuid is not None:
                try:
                    svc = dev.getServiceRecordForUUID_(sdp_uuid)
                    print(f"[mac] getServiceRecordForUUID -> {svc}")
                    if svc is not None:
                        res = svc.getRFCOMMChannelID_(None)
                        print(f"[mac] getRFCOMMChannelID -> {res}")
                        if isinstance(res, tuple):
                            err, cid = res
                        else:
                            err, cid = 0, res
                        if err == 0 and cid and cid > 0:
                            channel_id = cid
                except Exception as e:
                    print(f"[mac] sdp err: {e!r}")
                    channel_id = None

            if channel_id is None:
                services = _mac_call(dev, "services")
                if services:
                    print(f"[mac] services: {len(services)} записей")
                    for svc in services:
                        try:
                            sname = _mac_call(svc, "getServiceName") or _mac_call(svc, "serviceName") or ""
                            res = svc.getRFCOMMChannelID_(None)
                            if isinstance(res, tuple):
                                err, cid = res
                            else:
                                err, cid = 0, res
                            print(f"[mac]   svc name={sname!r} rfcomm={cid} err={err}")
                            if err == 0 and cid and cid > 0:
                                if channel_id is None:
                                    channel_id = cid
                                if "GAIA" in str(sname).upper() or "a2129ff3" in str(sname).lower():
                                    channel_id = cid
                                    print(f"[mac] GAIA3 найден по имени: {sname!r} канал {cid}")
                                    break
                        except Exception:
                            continue
                    if channel_id is not None:
                        print(f"[mac] channel_id из services: {channel_id}")

            if channel_id is None:
                print("[mac] SDP не дал канал, перебираю RFCOMM 1..20")
                for cid in range(1, 21):
                    res = dev.openRFCOMMChannelSync_withChannelID_delegate_(cid, None)
                    if isinstance(res, tuple):
                        err, ch = res
                    else:
                        err, ch = res, None
                    if err == 0 and ch is not None:
                        print(f"[mac] подошёл канал {cid}")
                        channel_id = cid
                        ch.closeChannel()
                        break
            if channel_id is None:
                channel_id = 1
                print("[mac] канал не найден, беру 1")
            self._channel_id = channel_id

            print(f"[mac] открываю RFCOMM канал {channel_id}")
            self._channel = self._open_channel(dev, channel_id)
            if self._channel is None:
                print(f"[mac] не удалось открыть канал {channel_id}")
                opened.set()
                return
            print("[mac] RFCOMM канал открыт")
            opened.set()

            # Keepalive: наушник закрывает SPP после ответа — держим канал открытым,
            # чтобы соединение не отваливалось от системы.
            while not self._closed:
                if self._channel is None:
                    print("[mac] канал закрыт, переоткрываю")
                    time.sleep(0.5)
                    ch = self._open_channel(dev, channel_id)
                    if ch is not None:
                        self._channel = ch
                        print("[mac] канал переоткрыт")
                NSRunLoop.currentRunLoop().runUntilDate_(
                    NSDate.dateWithTimeIntervalSinceNow_(0.1)
                )
        except Exception as e:
            print(f"[mac] исключение: {e!r}")
            opened.set()

    def _open_channel(self, dev, channel_id):
        """Открывает RFCOMM-канал, пробуя разные сигнатуры PyObjC. Возвращает канал или None."""
        import Foundation
        open_done = self._open_done
        open_done.clear()
        self._open_status = 0
        delegate = self._make_delegate()
        rfc_methods = [m for m in dir(dev) if "RFCOMM" in m and "open" in m.lower()]
        print(f"[mac] методы openRFCOMM: {rfc_methods}")
        channel = None
        for m in rfc_methods or [None]:
            if m is None:
                break
            fn = getattr(dev, m, None)
            if fn is None:
                continue
            # Пробуем разные сигнатуры: (cid, delegate), (None, cid, delegate), (cid, delegate, None)
            for args in ((channel_id, delegate), (None, channel_id, delegate), (channel_id, delegate, None)):
                try:
                    res = fn(*args)
                    print(f"[mac] {m}{[type(a).__name__ for a in args]} -> {res}")
                    if isinstance(res, tuple):
                        err, ch = res[0], (res[1] if len(res) > 1 else None)
                    else:
                        err, ch = res, None
                    if err == 0 and ch is not None:
                        channel = ch
                        break
                except TypeError as te:
                    print(f"[mac] {m}{[type(a).__name__ for a in args]} TypeError: {te}")
                    continue
                except Exception as e:
                    print(f"[mac] {m}{[type(a).__name__ for a in args]} err: {e!r}")
                    continue
            if channel is not None:
                break
        if channel is not None:
            if not open_done.wait(5.0):
                print("[mac] не дождался openComplete, пробую писать всё равно")
            elif self._open_status != 0:
                try:
                    channel.closeChannel()
                except Exception:
                    pass
                return None
        return channel

    async def send(self, gaia: bytes):
        buf = spp_frame(gaia)
        # Keepalive-поток переоткрывает канал после того, как наушник закрыл SPP.
        for _ in range(20):
            if self._channel is not None:
                break
            await asyncio.sleep(0.1)
        ch = self._channel
        if ch is None:
            raise OSError("RFCOMM-канал не открыт")
        import Foundation
        methods = [(n, getattr(ch, n)) for n in dir(ch) if n in ("writeSync_length_", "writeAsync_length_", "writeData_")]
        data = bytes(buf)
        nsdata = Foundation.NSData.dataWithBytes_length_(data, len(data))
        last = None
        for name, fn in methods:
            for payload, label in ((nsdata, "NSData"), (data, "bytes")):
                try:
                    res = fn(payload, len(data)) if not name.endswith("writeData_") else fn(payload)
                    print(f"[mac] {name}({label}) -> {res}")
                    if res == 0:
                        print("TX:", hexd(buf))
                        return
                    last = f"{name}({label})={res:#x}"
                except TypeError as te:
                    print(f"[mac] {name}({label}) TypeError: {te}")
                    last = te
                except Exception as e:
                    print(f"[mac] {name}({label}) err: {e!r}")
                    last = e
        raise OSError(f"write failed: {last}")

    def is_alive(self) -> bool:
        return not self._closed and self._channel is not None

    async def recv(self, timeout: float = 3.0) -> bytes:
        try:
            data = await asyncio.wait_for(self._q.get(), timeout)
        except asyncio.TimeoutError:
            return b""
        print("RX:", hexd(data))
        return data

    async def recv_frame(self, timeout: float = 3.0) -> bytes:
        deadline = time.monotonic() + timeout
        while True:
            frame = take_spp_frame(self._rx_buffer)
            if frame is not None:
                if frame:
                    print("RX:", hexd(frame))
                return frame
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return b""
            try:
                chunk = await asyncio.wait_for(self._q.get(), remaining)
            except asyncio.TimeoutError:
                return b""
            if not chunk:
                self._channel = None
                return b""
            self._rx_buffer.extend(chunk)

    async def close(self):
        self._closed = True
        try:
            if self._channel is not None:
                self._channel.closeChannel()
        except Exception:
            pass
        try:
            if self._device is not None:
                self._device.closeConnection()
        except Exception:
            pass


def create_transport(bt_addr: str) -> BaseSppTransport:
    system = platform.system()
    if system == "Windows":
        return WinSppTransport(bt_addr)
    elif system == "Darwin":
        return MacSppTransport(bt_addr)
    raise RuntimeError(f"Неподдерживаемая ОС: {system}")


def list_paired_devices():
    """Возвращает список сопряжённых Bluetooth-устройств: [{name, address}]."""
    system = platform.system()
    if system == "Windows":
        return _list_paired_windows()
    elif system == "Darwin":
        return _list_paired_macos()
    return []


def _list_paired_windows():
    if not HAS_WINRT:
        raise RuntimeError("На Windows нужны пакеты winrt (см. сообщение в connect).")
    from winrt.windows.devices.enumeration import DeviceInformation, DeviceClass
    import re

    try:
        selector = BluetoothDevice.get_device_selector()
        result = DeviceInformation.find_all_async_aqs_filter(selector).get()
    except Exception:
        result = DeviceInformation.find_all_async_device_class(DeviceClass.ALL).get()
    seen = {}
    for d in result:
        name = d.name or ""
        device_id = d.id or ""
        matches = re.findall(r"(?i)([0-9a-f]{2}(?:[:-][0-9a-f]{2}){5})", device_id)
        if not matches:
            matches = re.findall(r"(?i)([0-9a-f]{12})", device_id)
        if matches and name:
            a = re.sub(r"[:-]", "", matches[-1]).upper()
            addr = ":".join(a[i:i + 2] for i in range(0, 12, 2))
            seen.setdefault(addr, name)
    return [{"name": name, "address": addr} for addr, name in seen.items()]


def _mac_attr(obj, attr):
    """Получить строковое значение атрибута IOBluetooth (PyObjC).

    На новых версиях PyObjC `device.name` / `device.addressString`
    возвращают native-selector объекты — их нужно вызывать.
    """
    try:
        val = getattr(obj, attr)
    except Exception:
        return ""
    if callable(val):
        try:
            val = val()
        except Exception:
            return ""
    return str(val or "")


def _mac_sdp_uuid(uuid_str):
    """Создать IOBluetoothSDPUUID из строки UUID (имя метода зависит от версии PyObjC)."""
    for name in ("uuidWithUUIDString_", "uuidWithString_", "UUIDWithUUIDString_"):
        fn = getattr(IOBluetoothSDPUUID, name, None)
        if fn is not None:
            return fn(uuid_str)
    return None


def _mac_call(obj, attr):
    """Получить значение атрибута/метода PyObjC, вызывая selector при необходимости."""
    try:
        val = getattr(obj, attr)
    except Exception:
        return None
    if callable(val):
        try:
            return val()
        except Exception:
            return None
    return val


def _list_paired_macos():
    if not HAS_IOBT:
        raise RuntimeError("На macOS нужен PyObjC IOBluetooth: pip install pyobjc-framework-IOBluetooth")
    devs = IOBluetoothDevice.pairedDevices()
    out = []
    for d in devs:
        name = _mac_attr(d, "name")
        addr = _mac_attr(d, "addressString")
        # Единый формат с двоеточиями, как на Windows
        addr = addr.replace("-", ":").upper()
        out.append({"name": name, "address": addr})
    return out
