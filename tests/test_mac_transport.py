import asyncio
import importlib.util
import threading
import unittest
from collections import deque
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


class MacTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        registered = set()

        class NSObject:
            def __init_subclass__(cls):
                if cls.__name__ in registered:
                    raise RuntimeError("overriding existing Objective-C class")
                registered.add(cls.__name__)

            @classmethod
            def alloc(cls):
                return cls()

            def init(self):
                return self

        self.events = deque()
        self.native_threads = []
        self.run_loop = Mock()
        self.run_loop.runMode_beforeDate_.side_effect = self.pump
        objc = SimpleNamespace(
            registerMetaDataForSelector=Mock(),
            typedSelector=lambda signature: lambda method: method,
            autorelease_pool=nullcontext,
        )
        foundation = SimpleNamespace(
            NSObject=NSObject,
            NSRunLoop=SimpleNamespace(mainRunLoop=lambda: self.run_loop),
            NSDate=SimpleNamespace(dateWithTimeIntervalSinceNow_=lambda delay: delay),
            NSDefaultRunLoopMode="default",
        )
        self.device = Mock()
        service = Mock()
        service.getServiceName.return_value = "GAIA"
        service.getRFCOMMChannelID_.return_value = (0, 15)
        self.device.services.return_value = [service]
        bluetooth = SimpleNamespace(
            IOBluetoothDevice=SimpleNamespace(deviceWithAddressString_=lambda address: self.device),
            IOBluetoothSDPUUID=SimpleNamespace(),
        )
        spec = importlib.util.spec_from_file_location(
            "mac_transport_under_test", Path(__file__).resolve().parents[1] / "gaia_transport.py"
        )
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict("sys.modules", {"objc": objc, "Foundation": foundation, "IOBluetooth": bluetooth}):
            spec.loader.exec_module(self.module)
        self.transport = self.module.MacSppTransport("AA:BB:CC:DD:EE:FF")
        self.channel = Mock()
        self.channel.isOpen.return_value = False
        self.open_started = asyncio.Event()
        self.device.openRFCOMMChannelAsync_withChannelID_delegate_.side_effect = self.open_channel

    async def asyncTearDown(self):
        await self.transport.close()

    def pump(self, mode, date):
        self.native_threads.append(threading.current_thread())
        self.assertIs(threading.current_thread(), threading.main_thread())
        if self.events:
            self.events.popleft()()

    def open_channel(self, out, channel_id, delegate):
        self.native_threads.append(threading.current_thread())
        self.open_started.set()
        self.events.append(lambda: self.complete_open())
        return 0, self.channel

    def complete_open(self, status=0):
        self.channel.isOpen.return_value = status == 0
        self.transport._delegate.rfcommChannelOpenComplete_status_(self.channel, status)

    async def test_connect_and_receive_require_main_run_loop(self):
        await asyncio.wait_for(self.transport.connect(), 1.0)
        self.assertTrue(self.transport.is_alive())
        self.device.openRFCOMMChannelAsync_withChannelID_delegate_.assert_called_once_with(
            None, 15, self.transport._delegate
        )
        frame = self.module.spp_frame(b"\x04\x95\x1b\x05\x01")
        self.events.append(lambda: self.transport._delegate.rfcommChannelData_data_length_(
            self.channel, frame, len(frame)
        ))
        self.assertEqual(await self.transport.recv_frame(timeout=0.5), frame)
        self.assertTrue(all(thread is threading.main_thread() for thread in self.native_threads))
        self.device.openConnection.assert_not_called()

    async def test_reconnect_reuses_class_and_restarts_pump(self):
        await self.transport.connect()
        first = self.transport._delegate
        first_pump = self.transport._pump_task
        await self.transport.close()
        self.assertTrue(first_pump.done())
        await self.transport.connect()
        second = self.transport._delegate
        self.assertIs(type(first), type(second))
        self.assertIsNot(first, second)
        self.assertTrue(self.transport.is_alive())

    async def test_missing_callback_fails_even_if_native_channel_is_open(self):
        self.device.openRFCOMMChannelAsync_withChannelID_delegate_.side_effect = None
        self.device.openRFCOMMChannelAsync_withChannelID_delegate_.return_value = (0, self.channel)
        self.channel.isOpen.return_value = True
        with patch.object(self.module, "MAC_OPEN_TIMEOUT", 0.03):
            with self.assertRaisesRegex(TimeoutError, "openComplete timed out"):
                await self.transport.connect()
        self.assertFalse(self.transport.is_alive())
        self.assertIsNone(self.transport._pump_task)
        self.channel.closeChannel.assert_called_once()
        self.device.closeConnection.assert_not_called()

    async def test_failed_callback_fails_and_closes_channel(self):
        def fail_open():
            self.transport._delegate.rfcommChannelOpenComplete_status_(self.channel, -1)

        with patch.object(self, "complete_open", side_effect=fail_open):
            with self.assertRaisesRegex(OSError, "openComplete failed"):
                await self.transport.connect()
        self.channel.closeChannel.assert_called_once()
        self.assertFalse(self.transport.is_alive())

    async def test_data_is_copied_before_native_buffer_changes(self):
        await self.transport.connect()
        frame = self.module.spp_frame(b"\x04\x95\x1b\x05\x01")
        data = bytearray(frame + b"extra")
        self.transport._delegate.rfcommChannelData_data_length_(self.channel, data, len(frame))
        data[:] = bytes(len(data))
        self.assertEqual(await self.transport.recv_frame(timeout=0.1), frame)

    async def test_remote_close_unblocks_receive_without_reopening(self):
        await self.transport.connect()
        self.events.append(lambda: self.transport._delegate.rfcommChannelClosed_(self.channel))
        self.assertEqual(await self.transport.recv_frame(timeout=0.5), b"")
        self.assertFalse(self.transport.is_alive())
        self.device.openRFCOMMChannelAsync_withChannelID_delegate_.assert_called_once()
        with self.assertRaises(OSError):
            await self.transport.send(b"\x04\x95\x1a\x05")

    async def test_unrelated_rfcomm_service_is_not_opened(self):
        self.device.services.return_value[0].getServiceName.return_value = "Hands-Free unit"
        with self.assertRaisesRegex(OSError, "GAIA3 RFCOMM service not found"):
            await self.transport.connect()
        self.device.openRFCOMMChannelAsync_withChannelID_delegate_.assert_not_called()
        self.assertIsNone(self.transport._pump_task)

    async def test_cancelled_connect_closes_native_channel_and_pump(self):
        def open_without_callback(*args):
            self.open_started.set()
            return 0, self.channel

        self.device.openRFCOMMChannelAsync_withChannelID_delegate_.side_effect = open_without_callback
        task = asyncio.create_task(self.transport.connect())
        await asyncio.wait_for(self.open_started.wait(), 0.5)
        pump = self.transport._pump_task
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(pump.done())
        self.channel.setDelegate_.assert_called_once_with(None)
        self.channel.closeChannel.assert_called_once()
        self.assertFalse(self.transport.is_alive())

    async def test_background_thread_connect_fails_before_native_call(self):
        with self.assertRaisesRegex(RuntimeError, "main thread"):
            await asyncio.to_thread(lambda: asyncio.run(self.transport.connect()))
        self.device.openRFCOMMChannelAsync_withChannelID_delegate_.assert_not_called()

    async def test_native_pump_failure_unblocks_connect(self):
        self.run_loop.runMode_beforeDate_.side_effect = RuntimeError("run loop failed")
        with self.assertRaisesRegex(OSError, "closed while opening"):
            await asyncio.wait_for(self.transport.connect(), 0.5)
        self.channel.closeChannel.assert_called_once()
        self.assertFalse(self.transport.is_alive())

    async def test_device_listing_stays_on_main_thread(self):
        from bridge import Bridge, dispatch

        threads = []

        def list_devices():
            threads.append(threading.current_thread())
            return []

        with patch("bridge.platform.system", return_value="Darwin"), patch("bridge.list_paired_devices", side_effect=list_devices):
            self.assertEqual(await dispatch(Bridge(), {"cmd": "list"}), [])
        self.assertEqual(threads, [threading.main_thread()])


if __name__ == "__main__":
    unittest.main()
