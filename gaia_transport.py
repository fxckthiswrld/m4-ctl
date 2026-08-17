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
        self._rx_queue = asyncio.Queue()
        self._rx_task = None

    async def connect(self):
        if not HAS_WINRT:
            sys.exit("На Windows нужны пакеты winrt: pip install winrt-Windows.Devices.Bluetooth winrt-Windows.Devices.Bluetooth.Rfcomm winrt-Windows.Networking winrt-Windows.Networking.Sockets winrt-Windows.Storage.Streams")

        addr_int = int(self.bt_addr.replace(":", ""), 16)
        dev = BluetoothDevice.from_bluetooth_address_async(addr_int).get()
        if not dev or not dev.name:
            sys.exit(f"Устройство {self.bt_addr} не найдено (проверь сопряжение).")

        sid = RfcommServiceId.from_uuid(uuid.UUID(GAIA3_SPP_UUID))
        res = dev.get_rfcomm_services_for_id_async(sid).get()
        services = list(res.services)
        if not services:
            sys.exit("RFCOMM-сервис GAIA3 не найден на устройстве.")
        print(f"RFCOMM-сервисов GAIA3: {len(services)}")

        svc = services[0]
        host = HostName(svc.connection_host_name.raw_name)

        self.sock = StreamSocket()
        self.sock.connect_async(host, str(svc.connection_service_name)).get()
        print("SPP-соединение установлено.")

        self.reader = DataReader(self.sock.input_stream)
        self.reader.input_stream_options = InputStreamOptions.PARTIAL
        self._rx_task = asyncio.create_task(self._rx_loop())

    async def _rx_loop(self):
        while True:
            done = asyncio.Event()

            async def waiter():
                try:
                    await self.reader.load_async(1)
                    done.set()
                except Exception:
                    done.set()

            fut = asyncio.ensure_future(waiter())
            try:
                await asyncio.wait_for(done.wait(), None)
            except Exception:
                break
            n = self.reader.unconsumed_buffer_length
            if n == 0:
                await asyncio.sleep(0.05)
                continue
            data = bytes(self.reader.read_buffer(n))
            await self._rx_queue.put(data)

    async def recv(self, timeout: float = 3.0) -> bytes:
        try:
            data = await asyncio.wait_for(self._rx_queue.get(), timeout)
        except asyncio.TimeoutError:
            return b""
        print("RX:", hexd(data))
        return data

    async def recv_frame(self, timeout: float = 3.0) -> bytes:
        got = bytearray()
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                chunk = await asyncio.wait_for(self._rx_queue.get(), remaining)
            except asyncio.TimeoutError:
                break
            got.extend(chunk)
            if len(got) < 4:
                continue
            if got[0] != 0xFF or got[1] != 3:
                print("RX (raw):", hexd(bytes(got)))
                return b""
            total = 8 + got[3]
            if len(got) >= total:
                break
        if not got:
            return b""
        if got[0] != 0xFF or got[1] != 3 or len(got) < 4:
            print("RX (raw):", hexd(bytes(got)))
            return b""
        total = 8 + got[3]
        frame = bytes(got[:total])
        print("RX:", hexd(frame))
        return frame

    async def send(self, gaia: bytes):
        buf = spp_frame(gaia)
        writer = DataWriter(self.sock.output_stream)
        writer.write_bytes(buf)
        writer.store_async().get()
        print("TX:", hexd(buf))

    async def close(self):
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

    async def connect(self):
        if not HAS_IOBT:
            sys.exit("На macOS нужен PyObjC IOBluetooth: pip install pyobjc-framework-IOBluetooth")
        self._loop = asyncio.get_running_loop()
        self._q = asyncio.Queue()
        self._thread = threading.Thread(target=self._thread_main, daemon=True)
        self._thread.start()
        if not self._opened.wait(15.0):
            sys.exit("Не удалось открыть RFCOMM-канал GAIA3 на macOS.")
        print("SPP-соединение установлено.")

    def _thread_main(self):
        import Foundation
        from IOBluetooth import IOBluetoothDevice, IOBluetoothSDPUUID

        queue = self._q
        loop = self._loop
        opened = self._opened

        class Delegate(Foundation.NSObject):
            def rfcommChannelOpenComplete_status_(self, channel, status):
                opened.set()

            def rfcommChannelData_data_length_(self, channel, data, length):
                try:
                    raw = bytes(data)[:length]
                except Exception:
                    raw = b""
                loop.call_soon_threadsafe(queue.put_nowait, raw)

            def rfcommChannelClosed_(self, channel):
                loop.call_soon_threadsafe(queue.put_nowait, b"")

        try:
            # IOBluetooth ожидает формат с дефисами: 80-C3-BA-9C-A5-4F
            dev_addr = self.bt_addr.replace(":", "-")
            dev = IOBluetoothDevice.deviceWithAddressString_(dev_addr)
            if dev is None:
                loop.call_soon_threadsafe(opened.set)
                return
            status = dev.openConnection()
            if status != 0:
                loop.call_soon_threadsafe(opened.set)
                return
            self._device = dev

            sdp_uuid = IOBluetoothSDPUUID.uuidWithUUIDString_(GAIA3_SPP_UUID)
            channel_id = None
            try:
                svc = dev.getServiceRecordForUUID_(sdp_uuid)
                if svc is not None:
                    res = svc.getRFCOMMChannelID_(None)
                    if isinstance(res, tuple):
                        err, cid = res
                    else:
                        err, cid = 0, res
                    if err == 0 and cid and cid > 0:
                        channel_id = cid
            except Exception:
                channel_id = None

            if channel_id is None:
                for svc in dev.services or []:
                    try:
                        res = svc.getRFCOMMChannelID_(None)
                        if isinstance(res, tuple):
                            err, cid = res
                        else:
                            err, cid = 0, res
                        if err == 0 and cid and cid > 0:
                            channel_id = cid
                            break
                    except Exception:
                        continue

            if channel_id is None:
                channel_id = 1

            delegate = Delegate.alloc().init()
            self._delegate = delegate
            res = dev.openRFCOMMChannelSync_withChannelID_delegate_(channel_id, delegate)
            if isinstance(res, tuple):
                err, channel = res
            else:
                err, channel = res, None
            if err != 0 or channel is None:
                loop.call_soon_threadsafe(opened.set)
                return
            self._channel = channel

            while not self._closed:
                NSRunLoop.currentRunLoop().runUntilDate_(
                    NSDate.dateWithTimeIntervalSinceNow_(0.1)
                )
        except Exception as e:
            loop.call_soon_threadsafe(opened.set)

    async def send(self, gaia: bytes):
        buf = spp_frame(gaia)
        ch = self._channel
        if ch is None:
            raise OSError("RFCOMM-канал не открыт")
        err = ch.writeSync_data_length_(bytes(buf), len(buf))
        if err != 0:
            raise OSError(f"writeSync error={err}")
        print("TX:", hexd(buf))

    async def recv(self, timeout: float = 3.0) -> bytes:
        try:
            data = await asyncio.wait_for(self._q.get(), timeout)
        except asyncio.TimeoutError:
            return b""
        print("RX:", hexd(data))
        return data

    async def recv_frame(self, timeout: float = 3.0) -> bytes:
        got = bytearray()
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                chunk = await asyncio.wait_for(self._q.get(), remaining)
            except asyncio.TimeoutError:
                break
            if not chunk:
                break
            got.extend(chunk)
            if len(got) < 4:
                continue
            if got[0] != 0xFF or got[1] != 3:
                print("RX (raw):", hexd(bytes(got)))
                return b""
            total = 8 + got[3]
            if len(got) >= total:
                break
        if not got:
            return b""
        if got[0] != 0xFF or got[1] != 3 or len(got) < 4:
            print("RX (raw):", hexd(bytes(got)))
            return b""
        total = 8 + got[3]
        frame = bytes(got[:total])
        print("RX:", hexd(frame))
        return frame

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
    else:
        sys.exit(f"Неподдерживаемая ОС: {system}")


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
        sys.exit("На Windows нужны пакеты winrt (см. сообщение в connect).")
    from winrt.windows.devices.enumeration import DeviceInformation, DeviceClass
    import re

    result = DeviceInformation.find_all_async_device_class(DeviceClass.ALL).get()
    seen = {}
    for d in result:
        name = d.name or ""
        m = re.search(r"BluetoothDevice_([0-9A-Fa-f]{12})", d.id or "")
        if m:
            a = m.group(1).upper()
            addr = ":".join(a[i:i + 2] for i in range(0, 12, 2))
            seen.setdefault(addr, name)
            continue
        m = re.search(r"&([0-9A-Fa-f]{12})", d.id or "")
        if m and name:
            a = m.group(1).upper()
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


def _list_paired_macos():
    if not HAS_IOBT:
        sys.exit("На macOS нужен PyObjC IOBluetooth: pip install pyobjc-framework-IOBluetooth")
    devs = IOBluetoothDevice.pairedDevices()
    out = []
    for d in devs:
        name = _mac_attr(d, "name")
        addr = _mac_attr(d, "addressString")
        # Единый формат с двоеточиями, как на Windows
        addr = addr.replace("-", ":").upper()
        out.append({"name": name, "address": addr})
    return out