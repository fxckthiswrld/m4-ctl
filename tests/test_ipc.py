import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from gaia_transport import create_transport, take_spp_frame


ROOT = Path(__file__).resolve().parents[1]


class SppFramingTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
