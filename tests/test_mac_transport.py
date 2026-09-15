import asyncio
import importlib.util
import threading
import unittest
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

        self.run_loop = Mock()
        objc = SimpleNamespace(
            registerMetaDataForSelector=Mock(),
            typedSelector=lambda signature: lambda method: method,
        )
        foundation = SimpleNamespace(
            NSObject=NSObject,
            NSRunLoop=SimpleNamespace(currentRunLoop=lambda: self.run_loop),
            NSDate=SimpleNamespace(dateWithTimeIntervalSinceNow_=lambda delay: delay),
        )
        self.device = Mock()
        self.device.openConnection.return_value = 0
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
        self.transport._closed = False
        self.transport._loop = asyncio.get_running_loop()
        self.transport._q = asyncio.Queue()
        self.channel = Mock()
        self.device.openRFCOMMChannelAsync_withChannelID_delegate_.return_value = (0, self.channel)

    def complete_open(self, status=0):
        self.transport._delegate.rfcommChannelOpenComplete_status_(self.channel, status)

    async def test_reconnect_reuses_class_with_separate_instances(self):
        first = self.transport._make_delegate()
        other = self.module.MacSppTransport("00:11:22:33:44:55")
        second = other._make_delegate()
        self.assertIs(type(first), type(second))
        self.assertIsNot(first, second)
        self.assertIs(first.transport, self.transport)
        self.assertIs(second.transport, other)

    async def test_open_pumps_run_loop_until_callback(self):
        self.run_loop.runUntilDate_.side_effect = lambda date: self.complete_open()
        self.assertIs(self.transport._open_channel(self.device, 15), self.channel)
        self.run_loop.runUntilDate_.assert_called_once()
        self.device.openRFCOMMChannelAsync_withChannelID_delegate_.assert_called_once_with(
            None, 15, self.transport._delegate
        )

    async def test_missing_callback_fails_and_worker_closes_channel(self):
        with patch.object(self.module.time, "monotonic", side_effect=[0, 6]):
            self.transport._run_channel()
        self.assertIn("openComplete timed out", self.transport._open_error)
        self.assertFalse(self.transport.is_alive())
        self.assertTrue(self.transport._opened.is_set())
        self.channel.closeChannel.assert_called_once()
        self.device.closeConnection.assert_not_called()

    async def test_failed_callback_fails_and_closes_channel(self):
        self.run_loop.runUntilDate_.side_effect = lambda date: self.complete_open(-1)
        self.transport._run_channel()
        self.assertIn("openComplete failed", self.transport._open_error)
        self.channel.closeChannel.assert_called_once()
        self.assertFalse(self.transport.is_alive())

    async def test_data_is_copied_and_delivered_from_callback(self):
        delegate = self.transport._make_delegate()
        frame = self.module.spp_frame(b"\x04\x95\x1b\x05\x01")
        data = bytearray(frame + b"extra")
        delegate.rfcommChannelData_data_length_(self.channel, data, len(frame))
        data[:] = bytes(len(data))
        self.assertEqual(await self.transport.recv_frame(timeout=0.1), frame)

    async def test_remote_close_stops_worker_without_reopening(self):
        callbacks = iter([
            lambda: self.complete_open(),
            lambda: self.transport._delegate.rfcommChannelClosed_(self.channel),
        ])
        self.run_loop.runUntilDate_.side_effect = lambda date: next(callbacks)()
        self.transport._run_channel()
        self.assertFalse(self.transport.is_alive())
        self.device.openRFCOMMChannelAsync_withChannelID_delegate_.assert_called_once()
        self.channel.closeChannel.assert_called_once()
        self.assertEqual(await self.transport.recv_frame(timeout=0.1), b"")
        with self.assertRaises(OSError):
            await self.transport.send(b"\x04\x95\x1a\x05")

    async def test_unrelated_rfcomm_service_is_not_opened(self):
        self.device.services.return_value[0].getServiceName.return_value = "Hands-Free unit"
        self.transport._run_channel()
        self.assertIn("GAIA3 RFCOMM service not found", self.transport._open_error)
        self.device.openRFCOMMChannelAsync_withChannelID_delegate_.assert_not_called()

    async def test_cancelled_discovery_does_not_open_channel(self):
        def discover():
            self.transport._closed = True
            return self.device.services.return_value

        self.device.services.side_effect = discover
        self.transport._run_channel()
        self.assertIn("cancelled", self.transport._open_error)
        self.device.openRFCOMMChannelAsync_withChannelID_delegate_.assert_not_called()

    async def test_cancelled_connect_stops_and_joins_worker(self):
        started = threading.Event()

        def worker():
            started.set()
            while not self.transport._closed:
                threading.Event().wait(0.01)
            self.transport._opened.set()

        with patch.object(self.transport, "_thread_main", side_effect=worker):
            task = asyncio.create_task(self.transport.connect())
            self.assertTrue(await asyncio.to_thread(started.wait, 1.0))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertFalse(self.transport._thread.is_alive())
        self.assertFalse(self.transport.is_alive())


if __name__ == "__main__":
    unittest.main()
