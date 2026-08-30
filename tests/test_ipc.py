import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from bridge import Bridge, gaia_frame, normalize_bt_address, parse_state_value
from gaia_transport import create_transport, take_spp_frame


ROOT = Path(__file__).resolve().parents[1]


class SppFramingTests(unittest.TestCase):
    def test_bluetooth_address_is_normalized_and_validated(self):
        self.assertEqual(normalize_bt_address(" aa-bb-cc-dd-ee-ff "), "AA:BB:CC:DD:EE:FF")
        with self.assertRaises(ValueError):
            normalize_bt_address("not-an-address")

    def test_state_payload_is_decoded_and_raw_payload_is_preserved(self):
        response = {"cmd": 0x1A01, "payload": b"\x00\x03"}

        self.assertEqual(
            parse_state_value("mode", response),
            {"raw": "00 03", "cmd": 0x1A01, "code": 3, "name": "ADAPTIVE", "key": "adaptive"},
        )
        self.assertEqual(
            parse_state_value("transparency", {"cmd": 0x1A03, "payload": b"\x00\x64"}),
            {"raw": "00 64", "cmd": 0x1A03, "level": 100},
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

        self.assertEqual(take_spp_frame(buffer), b"")
        self.assertEqual(take_spp_frame(buffer), frame)


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
        response = b"\xff\x03\x00\x04\x04\x95\x1a\x05\x00\x00\x00\x01"
        transport = FakeTransport(responses=[response])
        bridge = Bridge()
        bridge.tr = transport

        result = await bridge.cmd_get()

        self.assertEqual(result["state"]["anc"]["raw"], "00 00 00 01")
        self.assertTrue(result["state"]["anc"]["enabled"])
        self.assertIsNone(result["state"]["mode"])
        self.assertIsNone(result["state"]["transparency"])
        self.assertIsNone(result["state"]["transparent_hearing"])


if __name__ == "__main__":
    unittest.main()
