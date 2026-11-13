import asyncio
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from bridge import Bridge, execute_request, gaia_frame, normalize_bt_address, parse_gaia_rsp, parse_state_value, run_request
from gaia_transport import create_transport, find_headphones, spp_frame, take_spp_frame


ROOT = Path(__file__).resolve().parents[1]

# Tripwire: any UNPATCHED create_transport call reaches the real WinRT stack
# and can leave an uncancellable task that hangs the suite. This must never
# fire; if it does, some test lets a background task outlive its patch.
import bridge as _bridge_module
_original_create_transport = _bridge_module.create_transport


def _guarded_create_transport(addr):
    import traceback
    print("\n[TRIPWIRE] real create_transport called", flush=True)
    traceback.print_stack()
    return _original_create_transport(addr)


_bridge_module.create_transport = _guarded_create_transport


class SppFramingTests(unittest.TestCase):
    def test_bluetooth_address_is_normalized_and_validated(self):
        self.assertEqual(normalize_bt_address(" aa-bb-cc-dd-ee-ff "), "AA:BB:CC:DD:EE:FF")
        with self.assertRaises(ValueError):
            normalize_bt_address("not-an-address")

    def test_state_payload_is_decoded_and_raw_payload_is_preserved(self):
        response = {"cmd": 0x1B01, "payload": b"\x01\x02\x02\x00\x03\x01"}

        self.assertEqual(
            parse_state_value("mode", response),
            {"raw": "01 02 02 00 03 01", "cmd": 0x1B01, "antiwind": 2, "name": "ADAPTIVE", "key": "adaptive"},
        )
        self.assertEqual(
            parse_state_value("transparency", {"cmd": 0x1B03, "payload": b"\x64"}),
            {"raw": "64", "cmd": 0x1B03, "level": 100},
        )

    def test_unsupported_platform_is_reported_as_exception(self):
        with patch("gaia_transport.platform.system", return_value="Linux"):
            with self.assertRaises(RuntimeError):
                create_transport("00:11:22:33:44:55")

    def test_frame_parser_preserves_coalesced_tail(self):
        first = b"\xff\x03\x00\x04\x04\x95\x1a\x05\x00\x00\x00\x01"
        second = b"\xff\x03\x00\x06\x04\x95\x1a\x01\x01\x00\x00\x00\x01\x02"
        buffer = bytearray(first + second)

        self.assertEqual(take_spp_frame(buffer), first)
        self.assertEqual(take_spp_frame(buffer), second)
        self.assertEqual(buffer, bytearray())

    def test_parser_discards_noise_before_next_frame(self):
        frame = b"\xff\x03\x00\x04\x04\x95\x1a\x05\x00\x00\x00\x01"
        buffer = bytearray(b"noise" + frame)

        self.assertEqual(take_spp_frame(buffer), frame)

    def test_marker_split_after_noise_is_preserved(self):
        frame = spp_frame(gaia_frame(0x1B05, b"\x01"))
        buffer = bytearray(b"noise" + frame[:1])
        self.assertIsNone(take_spp_frame(buffer))
        buffer.extend(frame[1:])
        self.assertEqual(take_spp_frame(buffer), frame)

    def test_modes_are_decoded_independently(self):
        for payload, key, wind in [(b"\x01\x01\x02\x00\x03\x00", "custom", 1),
                                   (b"\x03\x00\x01\x02\x02\x01", "comfort", 2)]:
            state = parse_state_value("mode", {"cmd": 0x1B01, "payload": payload})
            self.assertEqual((state["key"], state["antiwind"]), (key, wind))

    def test_malformed_states_are_not_interpreted_as_values(self):
        for name, payload in [("mode", b"\x00\x03"), ("anc", b"\x02"),
                              ("transparency", b"\x65"), ("anc", b"\x00\x01")]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                parse_state_value(name, {"cmd": 0x1B01, "payload": payload})

    def test_response_header_and_length_are_validated(self):
        frame = spp_frame(gaia_frame(0x1B05, b"\x01"))
        for invalid in (frame[:-1], frame + b"\x00", frame[:1] + b"\x01" + frame[2:]):
            self.assertIsNone(parse_gaia_rsp(invalid))


class BridgeJsonlTests(unittest.TestCase):
    def setUp(self):
        self.process = subprocess.Popen(
            [sys.executable, "-u", "bridge.py"],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        self.assertIsNotNone(self.process.stdin)
        self.assertIsNotNone(self.process.stdout)
        ready = self.process.stdout.readline()
        self.assertEqual(json.loads(ready), {"event": "ready"})

    def tearDown(self):
        if self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=3)
        if self.process.stdout:
            self.process.stdout.close()
        if self.process.stderr:
            self.process.stderr.close()

    def send(self, payload):
        self.process.stdin.write(json.dumps(payload) + "\n")
        self.process.stdin.flush()
        return json.loads(self.process.stdout.readline())

    def test_replies_echo_request_id(self):
        first = self.send({"id": "first", "cmd": "list"})
        second = self.send({"id": 42, "cmd": "unknown"})

        self.assertEqual(first["id"], "first")
        self.assertTrue(first["ok"])
        self.assertEqual(second["id"], 42)
        self.assertFalse(second["ok"])
        self.assertIn("unknown", second["error"])

    def test_multiple_in_flight_requests_keep_their_ids(self):
        self.process.stdin.write(json.dumps({"id": "a", "cmd": "list"}) + "\n")
        self.process.stdin.write(json.dumps({"id": "b", "cmd": "list"}) + "\n")
        self.process.stdin.flush()
        replies = [json.loads(self.process.stdout.readline()), json.loads(self.process.stdout.readline())]

        self.assertEqual({reply["id"] for reply in replies}, {"a", "b"})
        self.assertTrue(all(reply["ok"] for reply in replies))

    def test_legacy_request_without_id_still_works(self):
        reply = self.send({"cmd": "list"})

        self.assertTrue(reply["ok"])
        self.assertNotIn("id", reply)

    def test_non_object_request_returns_error(self):
        self.process.stdin.write("[]\n")
        self.process.stdin.flush()
        reply = json.loads(self.process.stdout.readline())

        self.assertFalse(reply["ok"])
        self.assertIn("JSON-объектом", reply["error"])

    def test_log_file_records_the_session_when_configured(self):
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            log_path = os.path.join(folder, "bridge.log")
            env = dict(os.environ, M4_BRIDGE_LOG_FILE=log_path)
            process = subprocess.Popen(
                [sys.executable, "-u", "bridge.py"],
                cwd=ROOT,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                env=env,
            )
            try:
                self.assertEqual(json.loads(process.stdout.readline()), {"event": "ready"})
                process.stdin.write(json.dumps({"id": "x", "cmd": "close"}) + "\n")
                process.stdin.flush()
                reply = json.loads(process.stdout.readline())
                self.assertTrue(reply["ok"])
                process.stdin.close()
                process.wait(timeout=5)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                for stream in (process.stdin, process.stdout, process.stderr):
                    try:
                        if stream is not None:
                            stream.close()
                    except Exception:
                        pass
            self.assertTrue(os.path.exists(log_path))
            with open(log_path, encoding="utf-8") as handle:
                self.assertIn("session closed by request", handle.read())


class FakeTransport:
    def __init__(self, responses=None, fail_send=False, alive=True):
        self.responses = list(responses or [])
        self.auto_ack = responses is None
        self.fail_send = fail_send
        self.alive = alive
        self.sent = []
        self.closed = False
        self.connected = False
        self.incoming = asyncio.Queue()
        for frame in self.responses:
            self.incoming.put_nowait(frame)

    async def connect(self):
        self.connected = True
        self.alive = True

    async def send(self, frame):
        if self.fail_send:
            self.fail_send = False
            self.alive = False
            raise OSError("channel closed")
        self.sent.append(frame)
        if self.auto_ack:
            command = int.from_bytes(frame[2:4], "big")
            self.incoming.put_nowait(spp_frame(gaia_frame(command | 0x0100)))

    async def recv_frame(self, timeout=3.0):
        try:
            return await asyncio.wait_for(self.incoming.get(), timeout)
        except asyncio.TimeoutError:
            return b""

    async def close(self):
        self.closed = True
        self.alive = False

    def is_alive(self):
        return self.alive


class BridgeTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_custom_sends_expected_sequence(self):
        transport = FakeTransport()
        bridge = Bridge()
        bridge.addr = "AA:BB:CC:DD:EE:FF"
        bridge.tr = transport

        with patch("bridge.asyncio.sleep", new=AsyncMock()):
            result = await bridge.cmd_custom()

        self.assertEqual(result, {"mode": "CUSTOM"})
        self.assertEqual(
            transport.sent,
            [
                gaia_frame(0x1804, b"\x00"),
                gaia_frame(0x1A04, b"\x01"),
                gaia_frame(0x1A00, b"\x03\x00"),
                gaia_frame(0x1A00, b"\x02\x00"),
            ],
        )

    async def test_custom_restores_saved_slider_level(self):
        transport = FakeTransport()
        bridge = Bridge()
        bridge.addr = "AA:BB:CC:DD:EE:FF"
        bridge.tr = transport

        with patch("bridge.asyncio.sleep", new=AsyncMock()):
            await bridge.cmd_custom(63)

        self.assertEqual(
            transport.sent,
            [
                gaia_frame(0x1804, b"\x00"),
                gaia_frame(0x1A04, b"\x01"),
                gaia_frame(0x1A00, b"\x03\x00"),
                gaia_frame(0x1A00, b"\x02\x00"),
                gaia_frame(0x1A02, bytes([63])),
            ],
        )

    async def test_custom_clamps_slider_level(self):
        transport = FakeTransport()
        bridge = Bridge()
        bridge.addr = "AA:BB:CC:DD:EE:FF"
        bridge.tr = transport

        with patch("bridge.asyncio.sleep", new=AsyncMock()):
            await bridge.cmd_custom(150)

        self.assertEqual(transport.sent[-1], gaia_frame(0x1A02, bytes([100])))

    async def test_send_error_reconnects_and_retries_once(self):
        first = FakeTransport(fail_send=True)
        second = FakeTransport()
        bridge = Bridge()
        bridge.addr = "AA:BB:CC:DD:EE:FF"

        with patch("bridge.create_transport", side_effect=[second]):
            bridge.tr = first
            result = await bridge.cmd_anc("on")

        self.assertEqual(result, {"anc": "ON"})
        self.assertTrue(first.closed)
        # Recovery subscribes to device notifications before retrying the write.
        self.assertEqual(
            second.sent,
            [
                gaia_frame(0x0007, b"\x00"),
                gaia_frame(0x0007, b"\x01"),
                gaia_frame(0x0007, b"\x02"),
                gaia_frame(0x0007, b"\x03"),
                gaia_frame(0x0007, b"\x04"),
                gaia_frame(0x0007, b"\x05"),
                gaia_frame(0x0007, b"\x12"),
                gaia_frame(0x1A04, b"\x01"),
            ],
        )

    async def test_connect_normalizes_address_before_transport(self):
        transport = FakeTransport()
        bridge = Bridge()
        with patch("bridge.create_transport", return_value=transport) as factory:
            result = await bridge.cmd_connect("aa-bb-cc-dd-ee-ff")

        self.assertEqual(result["addr"], "AA:BB:CC:DD:EE:FF")
        factory.assert_called_once_with("AA:BB:CC:DD:EE:FF")

    async def test_connect_without_address_discovers_sennheiser(self):
        transport = FakeTransport()
        bridge = Bridge()
        with (
            patch("bridge.find_headphones", return_value=[{"name": "MOMENTUM 4 Wireless", "address": "AA:BB:CC:DD:EE:FF"}]),
            patch("bridge.create_transport", return_value=transport) as factory,
        ):
            result = await bridge.cmd_connect("")

        self.assertEqual(result["addr"], "AA:BB:CC:DD:EE:FF")
        factory.assert_called_once_with("AA:BB:CC:DD:EE:FF")

    async def test_connect_without_address_prefers_previous_device(self):
        transport = FakeTransport()
        bridge = Bridge()
        bridge.addr = "11:22:33:44:55:66"
        with (
            patch(
                "bridge.find_headphones",
                return_value=[
                    {"name": "MOMENTUM 4 Wireless", "address": "AA:BB:CC:DD:EE:FF"},
                    {"name": "Sennheiser MOMENTUM", "address": "11:22:33:44:55:66"},
                ],
            ),
            patch("bridge.create_transport", return_value=transport) as factory,
        ):
            result = await bridge.cmd_connect("")

        self.assertEqual(result["addr"], "11:22:33:44:55:66")
        factory.assert_called_once_with("11:22:33:44:55:66")

    async def test_connect_without_address_fails_without_paired_headphones(self):
        bridge = Bridge()
        with patch("bridge.find_headphones", return_value=[]):
            with self.assertRaises(RuntimeError):
                await bridge.cmd_connect("")
        self.assertIsNone(bridge.tr)

    def test_find_headphones_filters_and_sorts_by_name(self):
        with patch(
            "gaia_transport.list_paired_devices",
            return_value=[
                {"name": "WH-1000XM5", "address": "AA:AA:AA:AA:AA:AA"},
                {"name": "MOMENTUM 4 Wireless", "address": "AA:BB:CC:DD:EE:FF"},
                {"name": "sennheiser BTD 800", "address": "11:11:11:11:11:11"},
                {"name": "", "address": "22:22:22:22:22:22"},
            ],
        ):
            devices = find_headphones()

        self.assertEqual(
            devices,
            [
                {"name": "MOMENTUM 4 Wireless", "address": "AA:BB:CC:DD:EE:FF"},
                {"name": "sennheiser BTD 800", "address": "11:11:11:11:11:11"},
            ],
        )

    async def test_transparent_hearing_set(self):
        transport = FakeTransport()
        bridge = Bridge()
        bridge.addr = "AA:BB:CC:DD:EE:FF"
        bridge.tr = transport

        result = await bridge.cmd_transparent_hearing("on")

        self.assertEqual(result, {"transparent_hearing": "ON"})
        self.assertEqual(transport.sent, [gaia_frame(0x1804, b"\x01")])

    async def test_get_keeps_state_slots_when_a_response_is_missing(self):
        response = spp_frame(gaia_frame(0x1B05, b"\x01"))
        transport = FakeTransport(responses=[response])
        bridge = Bridge()
        bridge.tr = transport

        with patch("bridge.RESPONSE_TIMEOUT", 0.05):
            result = await bridge.cmd_get()

        self.assertEqual(result["state"]["anc"]["raw"], "01")
        self.assertTrue(result["state"]["anc"]["enabled"])
        self.assertIsNone(result["state"]["mode"])
        self.assertIsNone(result["state"]["transparency"])
        self.assertIsNone(result["state"]["transparent_hearing"])

    async def test_missing_ack_fails_command_but_keeps_channel_open(self):
        bridge = Bridge()
        bridge.tr = transport = FakeTransport(responses=[])
        with patch("bridge.RESPONSE_TIMEOUT", 0.05):
            with self.assertRaisesRegex(TimeoutError, "no acknowledgement"):
                await bridge.cmd_anc("on")
        self.assertFalse(transport.closed)
        self.assertIs(bridge.tr, transport)

    async def test_late_reply_after_timeout_cannot_close_or_ack_anything(self):
        bridge = Bridge()
        bridge.tr = transport = FakeTransport(responses=[])
        with patch("bridge.RESPONSE_TIMEOUT", 0.05):
            with self.assertRaisesRegex(TimeoutError, "no acknowledgement"):
                await bridge.cmd_anc("on")
        # The reply arrives only after the command gave up; the pump must drop it.
        transport.incoming.put_nowait(spp_frame(gaia_frame(0x1B04)))
        await asyncio.sleep(0.05)
        self.assertFalse(transport.closed)
        self.assertIs(bridge.tr, transport)
        self.assertNotIn(0x1A04, bridge._pending)

    async def test_notification_is_published_as_an_event(self):
        events = []
        bridge = Bridge(on_event=events.append)
        bridge.tr = FakeTransport(responses=[
            spp_frame(gaia_frame(0x1A85, b"\x01")),
            spp_frame(gaia_frame(0x1B04)),
        ])
        self.assertEqual(await bridge.cmd_anc("on"), {"anc": "ON"})
        self.assertIn({"event": "notification", "id": "0x1A85", "payload": "01"}, events)

    async def test_mode_notification_is_decoded_into_a_device_mode_event(self):
        # Verified on hardware: double-tap pushes 0x0482 with the new mode byte
        # (2 = comfort, 3 = adaptive, same enum as the ANC modes).
        events = []
        bridge = Bridge(on_event=events.append)
        bridge.tr = FakeTransport(responses=[
            spp_frame(gaia_frame(0x0482, b"\x03")),
            spp_frame(gaia_frame(0x1B04)),
        ])
        self.assertEqual(await bridge.cmd_anc("on"), {"anc": "ON"})
        self.assertIn({"event": "notification", "id": "0x0482", "payload": "03"}, events)
        self.assertIn({"event": "device-mode", "mode": "adaptive"}, events)

    async def test_unknown_mode_notification_is_forwarded_without_decoding(self):
        events = []
        bridge = Bridge(on_event=events.append)
        bridge.tr = FakeTransport(responses=[
            spp_frame(gaia_frame(0x0482, b"\x99")),
            spp_frame(gaia_frame(0x1B04)),
        ])
        self.assertEqual(await bridge.cmd_anc("on"), {"anc": "ON"})
        self.assertNotIn({"event": "device-mode", "mode": "adaptive"}, events)
        self.assertTrue(any(e.get("event") == "notification" for e in events))

    async def test_notification_and_other_command_do_not_acknowledge_write(self):
        bridge = Bridge()
        bridge.tr = FakeTransport(responses=[
            spp_frame(gaia_frame(0x1A85, b"\x01")),
            spp_frame(gaia_frame(0x1B00)),
            spp_frame(gaia_frame(0x1B04)),
        ])
        self.assertEqual(await bridge.cmd_anc("on"), {"anc": "ON"})
        self.assertTrue(bridge.tr.incoming.empty())

    async def test_device_error_is_not_success(self):
        bridge = Bridge()
        bridge.tr = FakeTransport(responses=[spp_frame(gaia_frame(0x1B84, b"\x02"))])
        with self.assertRaisesRegex(RuntimeError, "rejected"):
            await bridge.cmd_anc("on")

    async def test_other_vendor_or_request_echo_cannot_confirm_a_command(self):
        bridge = Bridge()
        bridge.tr = FakeTransport(responses=[
            spp_frame(b"\x00\x01\x1b\x04"),
            spp_frame(gaia_frame(0x1A04, b"\x01")),
        ])
        with patch("bridge.RESPONSE_TIMEOUT", 0.05):
            with self.assertRaises(TimeoutError):
                await bridge.cmd_anc("on")

    async def test_receive_exception_discards_channel(self):
        bridge = Bridge()
        bridge.tr = transport = FakeTransport()
        transport.recv_frame = AsyncMock(side_effect=OSError("receive failed"))
        with self.assertRaises(OSError):
            await bridge.cmd_anc("on")
        self.assertTrue(transport.closed)

    async def test_adaptive_clears_comfort_and_comfort_clears_adaptive(self):
        for mode, cleared in [("adaptive", 2), ("comfort", 3)]:
            bridge = Bridge()
            bridge.tr = transport = FakeTransport()
            with patch("bridge.asyncio.sleep", new=AsyncMock()):
                await bridge.cmd_mode(mode)
            self.assertIn(gaia_frame(0x1A00, bytes([cleared, 0])), transport.sent)

    async def test_no_state_responses_is_an_error(self):
        bridge = Bridge()
        bridge.tr = FakeTransport(responses=[])
        with patch("bridge.RESPONSE_TIMEOUT", 0.05):
            with self.assertRaisesRegex(RuntimeError, "state unavailable"):
                await bridge.cmd_get()

    async def test_state_timeout_does_not_reconnect_for_remaining_getters(self):
        bridge = Bridge()
        bridge.addr = "AA:BB:CC:DD:EE:FF"
        bridge.tr = transport = FakeTransport(responses=[])
        with patch("bridge.create_transport") as factory:
            with patch("bridge.RESPONSE_TIMEOUT", 0.05):
                with self.assertRaisesRegex(RuntimeError, "state unavailable"):
                    await bridge.cmd_get()
        factory.assert_not_called()
        self.assertEqual(transport.sent, [gaia_frame(0x1A05)])
        self.assertFalse(transport.closed)

    async def test_burst_responses_survive_the_registration_gap(self):
        # Regression (CI, Python 3.14 eager tasks): the pump may consume a whole
        # response burst before the next command registers its future. Replies
        # must be replayed from the buffer, never dropped.
        bridge = Bridge()
        bridge.tr = FakeTransport(responses=[
            spp_frame(gaia_frame(0x0703, b"\x52")),
            spp_frame(gaia_frame(0x1302, b"\x03\x12\x00")),
        ])
        await asyncio.sleep(0.1)  # let the pump chew through the burst up front
        result = await bridge.cmd_info()
        self.assertEqual(result, {"battery": [82], "firmware": "3.18.0", "errors": {}})

    async def test_info_timeout_does_not_reconnect_for_firmware(self):
        bridge = Bridge()
        bridge.addr = "AA:BB:CC:DD:EE:FF"
        bridge.tr = transport = FakeTransport(responses=[])
        with patch("bridge.create_transport") as factory:
            with patch("bridge.INFO_RESPONSE_TIMEOUT", 0.05):
                result = await bridge.cmd_info()
        factory.assert_not_called()
        self.assertEqual(transport.sent, [gaia_frame(0x0603)])
        self.assertEqual(set(result["errors"]), {"battery", "firmware"})

    async def test_expired_request_never_reaches_device(self):
        bridge = Bridge()
        bridge.tr = transport = FakeTransport()
        with self.assertRaisesRegex(TimeoutError, "expired"):
            await execute_request(bridge, {"cmd": "anc", "state": "on", "deadline_ms": 0})
        self.assertEqual(transport.sent, [])

    async def test_timeout_cancels_dispatch_and_closes_channel(self):
        bridge = Bridge()
        bridge.tr = transport = FakeTransport()
        cancelled = asyncio.Event()

        async def hang(*args):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with patch("bridge.COMMAND_TIMEOUT", 0.01), patch("bridge.dispatch", side_effect=hang):
            with self.assertRaises(asyncio.TimeoutError):
                await execute_request(bridge, {"cmd": "anc"})
        self.assertTrue(cancelled.is_set())
        self.assertTrue(transport.closed)

    async def test_cancelled_connect_closes_candidate_transport(self):
        bridge = Bridge()
        transport = FakeTransport()
        transport.connect = AsyncMock(side_effect=asyncio.CancelledError)
        with patch("bridge.create_transport", return_value=transport):
            with self.assertRaises(asyncio.CancelledError):
                await bridge.cmd_connect("AA:BB:CC:DD:EE:FF")
        self.assertTrue(transport.closed)

    async def test_custom_profile_applies_all_values_and_checks_readback(self):
        bridge = Bridge()
        bridge.tr = transport = FakeTransport()
        expected = {"state": {"anc": {"enabled": True}, "mode": {"key": "custom", "antiwind": 2}, "transparency": {"level": 40}}}
        bridge.cmd_get = AsyncMock(return_value=expected)
        with patch("bridge.asyncio.sleep", new=AsyncMock()):
            self.assertEqual(await bridge.cmd_profile("custom", 2, 40), expected)
        self.assertIn(gaia_frame(0x1A00, b"\x01\x02"), transport.sent)
        self.assertEqual(transport.sent[-1], gaia_frame(0x1A02, b"\x28"))
        bridge.cmd_get.assert_awaited_once()

    async def test_profile_requires_matching_readback(self):
        bridge = Bridge()
        bridge.tr = FakeTransport()
        bridge.cmd_get = AsyncMock(return_value={"state": {"anc": {"enabled": True}}})
        with self.assertRaisesRegex(RuntimeError, "not confirmed"):
            await bridge.cmd_profile("off", 0, 0)

    async def test_invalid_profile_is_rejected_before_any_write(self):
        for values in [("invalid", 0, 0), ("custom", 3, 0), ("custom", 0, 101), ("custom", True, 0)]:
            bridge = Bridge()
            bridge.tr = transport = FakeTransport()
            with self.assertRaises(ValueError):
                await bridge.cmd_profile(*values)
            self.assertEqual(transport.sent, [])

    async def test_recover_reconnects_when_channel_is_lost(self):
        bridge = Bridge()
        bridge.addr = "AA:BB:CC:DD:EE:FF"
        bridge.tr = None
        replacement = FakeTransport()
        with patch("bridge.create_transport", side_effect=[replacement]):
            await bridge._recover()
        self.assertIs(bridge.tr, replacement)
        self.assertTrue(replacement.connected)
        await bridge.aclose()

    async def test_watchdog_detects_silent_channel_and_recovers(self):
        events = []
        bridge = Bridge(on_event=events.append)
        bridge.addr = "AA:BB:CC:DD:EE:FF"
        dead = FakeTransport(responses=[])  # accepts writes, never answers
        bridge.tr = dead
        bridge._auto_reconnect = True
        bridge._last_rx = asyncio.get_running_loop().time() - 1000
        replacement = FakeTransport()
        with patch("bridge.create_transport", side_effect=[replacement]), \
             patch("bridge.WATCHDOG_TICK", 0.01), \
             patch("bridge.RESPONSE_TIMEOUT", 0.05), \
             patch("bridge.PING_RESPONSE_TIMEOUT", 0.05), \
             patch("bridge.LIVENESS_RETRY_DELAY", 0.02):
            bridge._ensure_watchdog()
            for _ in range(400):
                await asyncio.sleep(0.02)
                if bridge.tr is replacement:
                    break
            self.assertIs(bridge.tr, replacement)
            self.assertTrue(dead.closed)
            self.assertIn({"event": "reconnected"}, events)
        await bridge.aclose()

    async def test_pump_death_triggers_background_recovery(self):
        events = []
        bridge = Bridge(on_event=events.append)
        bridge.addr = "AA:BB:CC:DD:EE:FF"
        dead = FakeTransport()
        dead.recv_frame = AsyncMock(side_effect=OSError("receive failed"))
        bridge.tr = dead
        bridge._auto_reconnect = True
        replacement = FakeTransport()
        with patch("bridge.create_transport", side_effect=[replacement]), \
             patch("bridge.BACKGROUND_RECONNECT_START", 0.01):
            with self.assertRaises(OSError):
                await bridge.cmd_anc("on")
            self.assertTrue(dead.closed)
            for _ in range(200):
                await asyncio.sleep(0.02)
                if bridge.tr is replacement:
                    break
            self.assertIs(bridge.tr, replacement)
            self.assertIn({"event": "reconnected"}, events)
        await bridge.aclose()

    async def test_background_recovery_gives_up_and_reports_unreachable(self):
        events = []
        bridge = Bridge(on_event=events.append)
        bridge.addr = "AA:BB:CC:DD:EE:FF"
        bridge.tr = None
        bridge._auto_reconnect = True
        with patch("bridge.create_transport", side_effect=OSError("no transport")), \
             patch("bridge.BACKGROUND_RECONNECT_START", 0.01), \
             patch("bridge.BACKGROUND_RECONNECT_WINDOW", 0.05):
            await bridge._background_reconnect()
        self.assertFalse(bridge._auto_reconnect)
        self.assertIn({"event": "unreachable"}, events)

    async def test_explicit_close_disables_background_recovery(self):
        bridge = Bridge()
        bridge.addr = "AA:BB:CC:DD:EE:FF"
        bridge.tr = FakeTransport()
        bridge._auto_reconnect = True
        await bridge.cmd_close()
        self.assertFalse(bridge._auto_reconnect)
        self.assertIsNone(bridge.tr)

    async def test_multi_step_command_stops_at_expired_deadline(self):
        bridge = Bridge()
        bridge.tr = transport = FakeTransport()
        with patch("bridge.asyncio.sleep", new=AsyncMock()):
            with self.assertRaisesRegex(TimeoutError, "expired"):
                await bridge.cmd_transparency(40, deadline_ms=0)
        # Only the preparatory write left; the slider write was guarded by the deadline.
        self.assertEqual(transport.sent, [gaia_frame(0x1A00, b"\x03\x00")])

    async def test_custom_sequence_stops_at_expired_deadline(self):
        bridge = Bridge()
        bridge.tr = transport = FakeTransport()
        with patch("bridge.asyncio.sleep", new=AsyncMock()):
            with self.assertRaisesRegex(TimeoutError, "expired"):
                await bridge.cmd_custom(deadline_ms=0)
        self.assertEqual(transport.sent, [gaia_frame(0x1804, b"\x00")])

    async def test_cancel_request_aborts_the_in_flight_command(self):
        bridge = Bridge()
        bridge.tr = transport = FakeTransport()
        bridge.addr = "AA:BB:CC:DD:EE:FF"
        emitted = []

        async def hang(*args, **kwargs):
            await asyncio.Event().wait()

        with patch("bridge.dispatch", side_effect=hang):
            task = asyncio.create_task(run_request(bridge, emitted.append, "req", {"cmd": "anc", "state": "on"}))
            await asyncio.sleep(0.02)
            self.assertEqual(await bridge.cmd_cancel(None), {"cancelled": ["req"]})
            await asyncio.wait_for(asyncio.shield(task), 1.0)
        self.assertEqual(
            emitted,
            [{"ok": False, "error": "cancelled", "cancelled": True, "id": "req"}],
        )
        self.assertTrue(transport.closed)
        self.assertEqual(bridge._active, {})

    async def test_cancel_with_target_only_stops_that_request(self):
        bridge = Bridge()
        bridge._active = {"a": AsyncMock(), "b": AsyncMock()}
        self.assertEqual(await bridge.cmd_cancel("a"), {"cancelled": ["a"]})
        bridge._active["a"].cancel.assert_called_once()
        bridge._active["b"].cancel.assert_not_called()

    async def test_connect_registers_device_notifications(self):
        bridge = Bridge()
        transport = FakeTransport()
        with patch("bridge.create_transport", side_effect=[transport]):
            await bridge.cmd_connect("AA:BB:CC:DD:EE:FF")
        self.assertEqual(
            transport.sent,
            [
                gaia_frame(0x0007, b"\x00"),
                gaia_frame(0x0007, b"\x01"),
                gaia_frame(0x0007, b"\x02"),
                gaia_frame(0x0007, b"\x03"),
                gaia_frame(0x0007, b"\x04"),
                gaia_frame(0x0007, b"\x05"),
                gaia_frame(0x0007, b"\x12"),
            ],
        )

    async def test_notification_registration_failure_keeps_the_session(self):
        bridge = Bridge()
        bridge.addr = "AA:BB:CC:DD:EE:FF"
        transport = FakeTransport(responses=[])  # never acknowledges anything
        with patch("bridge.create_transport", side_effect=[transport]):
            with patch("bridge.RESPONSE_TIMEOUT", 0.05):
                result = await bridge.cmd_connect("AA:BB:CC:DD:EE:FF")
        self.assertEqual(result["connected"], True)
        self.assertIs(bridge.tr, transport)
        self.assertFalse(transport.closed)

    async def test_frame_parser_never_fails_on_arbitrary_noise(self):
        # Deterministic property-style fuzz: noise, valid frames, and their
        # fragments interleaved must never raise and only yield valid frames.
        import random

        rng = random.Random(20241108)
        valid = spp_frame(gaia_frame(0x1B05, b"\x01")) + spp_frame(gaia_frame(0x1B01, b"\x01\x02\x02\x00\x03\x01"))
        for _ in range(300):
            buffer = bytearray()
            for _ in range(rng.randint(1, 12)):
                kind = rng.random()
                if kind < 0.35:
                    buffer.extend(bytes(rng.randint(0, 255) for _ in range(rng.randint(1, 20))))
                elif kind < 0.8:
                    buffer.extend(valid)
                else:
                    fragment = valid[rng.randint(0, len(valid) - 1):]
                    buffer.extend(fragment[:rng.randint(1, len(fragment))])
            produced = bytearray()
            while True:
                try:
                    frame = take_spp_frame(buffer)
                except Exception as error:  # the parser must never raise
                    self.fail(f"take_spp_frame raised {error!r}")
                if frame is None:
                    break
                produced.extend(frame)
                self.assertEqual(len(frame), 8 + frame[3])
                self.assertEqual(frame[:3], b"\xff\x03\x00")
            self.assertLessEqual(len(produced), len(valid) * 12)

    async def test_watchdog_keeps_channel_when_frames_arrive_during_failed_ping(self):
        # Real hardware delays acks after notification bursts; frames arriving
        # while a ping fails prove the channel is alive and must be kept.
        bridge = Bridge()
        bridge.addr = "AA:BB:CC:DD:EE:FF"
        transport = FakeTransport()
        bridge.tr = transport
        bridge._auto_reconnect = True
        bridge._last_rx = asyncio.get_running_loop().time() - 1000

        ping_attempts = 0

        async def failing_ping():
            nonlocal ping_attempts
            ping_attempts += 1
            if ping_attempts == 1:
                # A notification frame lands while the ping is failing.
                bridge._rx_frames += 1
                bridge._last_rx = asyncio.get_running_loop().time()
            raise TimeoutError("no acknowledgement")

        with patch("bridge.create_transport") as factory, \
             patch.object(Bridge, "_ping", side_effect=failing_ping), \
             patch("bridge.WATCHDOG_TICK", 0.01), \
             patch("bridge.LIVENESS_RETRY_DELAY", 0.02):
            bridge._ensure_watchdog()
            await asyncio.sleep(0.15)
        self.assertFalse(transport.closed)
        self.assertIs(bridge.tr, transport)
        self.assertEqual(ping_attempts, 1, "frame evidence is checked right after the first failed ping")
        factory.assert_not_called()
        await bridge.aclose()

    async def test_watchdog_drops_channel_only_after_all_retries(self):
        bridge = Bridge()
        bridge.addr = "AA:BB:CC:DD:EE:FF"
        transport = FakeTransport()
        bridge.tr = transport
        bridge._auto_reconnect = False  # no background recovery in this test
        bridge._last_rx = asyncio.get_running_loop().time() - 1000
        ping_attempts = 0

        async def failing_ping():
            nonlocal ping_attempts
            ping_attempts += 1
            raise TimeoutError("no acknowledgement")

        with patch.object(Bridge, "_ping", side_effect=failing_ping), \
             patch("bridge.WATCHDOG_TICK", 0.01), \
             patch("bridge.LIVENESS_RETRY_DELAY", 0.01):
            bridge._ensure_watchdog()
            for _ in range(100):
                await asyncio.sleep(0.02)
                if transport.closed:
                    break
        self.assertTrue(transport.closed)
        self.assertIsNone(bridge.tr)
        self.assertEqual(ping_attempts, 3, "channel is dropped only after every ping retry")
        await bridge.aclose()

    async def test_info_decodes_battery_and_firmware(self):
        bridge = Bridge()
        bridge.tr = FakeTransport(responses=[spp_frame(gaia_frame(0x0703, b"\x52")), spp_frame(gaia_frame(0x1302, b"\x03\x12\x00"))])
        result = await bridge.cmd_info()
        self.assertEqual(result, {"battery": [82], "firmware": "3.18.0", "errors": {}})

    async def test_unsupported_battery_does_not_hide_firmware(self):
        bridge = Bridge()
        bridge.tr = FakeTransport(responses=[spp_frame(gaia_frame(0x0783, b"\x01")), spp_frame(gaia_frame(0x1302, b"\x03\x12\x00"))])
        result = await bridge.cmd_info()
        self.assertIsNone(result["battery"])
        self.assertEqual(result["firmware"], "3.18.0")
        self.assertIn("battery", result["errors"])


if __name__ == "__main__":
    unittest.main()
