import asyncio
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from bridge import Bridge, execute_request, gaia_frame, normalize_bt_address, parse_gaia_rsp, parse_state_value
from gaia_transport import create_transport, spp_frame, take_spp_frame


ROOT = Path(__file__).resolve().parents[1]


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


class FakeTransport:
    def __init__(self, responses=None, fail_send=False, alive=True):
        self.responses = list(responses or [])
        self.auto_ack = responses is None
        self.fail_send = fail_send
        self.alive = alive
        self.sent = []
        self.closed = False
        self.connected = False

    async def connect(self):
        self.connected = True
        self.alive = True

    async def send(self, frame):
        if self.fail_send:
            self.fail_send = False
            self.alive = False
            raise OSError("channel closed")
        self.sent.append(frame)

    async def recv_frame(self, timeout=3.0):
        if self.auto_ack:
            command = int.from_bytes(self.sent[-1][2:4], "big")
            return spp_frame(gaia_frame(command | 0x0100))
        return self.responses.pop(0) if self.responses else b""

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
                gaia_frame(0x1A02, b"\x00"),
            ],
        )

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
        self.assertEqual(second.sent, [gaia_frame(0x1A04, b"\x01")])

    async def test_connect_normalizes_address_before_transport(self):
        transport = FakeTransport()
        bridge = Bridge()
        with patch("bridge.create_transport", return_value=transport) as factory:
            result = await bridge.cmd_connect("aa-bb-cc-dd-ee-ff")

        self.assertEqual(result["addr"], "AA:BB:CC:DD:EE:FF")
        factory.assert_called_once_with("AA:BB:CC:DD:EE:FF")

    async def test_get_keeps_state_slots_when_a_response_is_missing(self):
        response = spp_frame(gaia_frame(0x1B05, b"\x01"))
        transport = FakeTransport(responses=[response])
        bridge = Bridge()
        bridge.tr = transport

        result = await bridge.cmd_get()

        self.assertEqual(result["state"]["anc"]["raw"], "01")
        self.assertTrue(result["state"]["anc"]["enabled"])
        self.assertIsNone(result["state"]["mode"])
        self.assertIsNone(result["state"]["transparency"])
        self.assertIsNone(result["state"]["transparent_hearing"])

    async def test_missing_ack_fails_command_and_closes_channel(self):
        bridge = Bridge()
        bridge.tr = transport = FakeTransport(responses=[])
        with self.assertRaisesRegex(TimeoutError, "no acknowledgement"):
            await bridge.cmd_anc("on")
        self.assertTrue(transport.closed)
        self.assertIsNone(bridge.tr)

    async def test_notification_and_other_command_do_not_acknowledge_write(self):
        bridge = Bridge()
        bridge.tr = FakeTransport(responses=[
            spp_frame(gaia_frame(0x1A85, b"\x01")),
            spp_frame(gaia_frame(0x1B00)),
            spp_frame(gaia_frame(0x1B04)),
        ])
        self.assertEqual(await bridge.cmd_anc("on"), {"anc": "ON"})
        self.assertEqual(bridge.tr.responses, [])

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
        with self.assertRaisesRegex(RuntimeError, "state unavailable"):
            await bridge.cmd_get()

    async def test_state_timeout_does_not_reconnect_for_remaining_getters(self):
        bridge = Bridge()
        bridge.addr = "AA:BB:CC:DD:EE:FF"
        bridge.tr = transport = FakeTransport(responses=[])
        with patch("bridge.create_transport") as factory:
            with self.assertRaisesRegex(RuntimeError, "state unavailable"):
                await bridge.cmd_get()
        factory.assert_not_called()
        self.assertEqual(transport.sent, [gaia_frame(0x1A05)])

    async def test_info_timeout_does_not_reconnect_for_firmware(self):
        bridge = Bridge()
        bridge.addr = "AA:BB:CC:DD:EE:FF"
        bridge.tr = transport = FakeTransport(responses=[])
        with patch("bridge.create_transport") as factory:
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
