"""Check an executable's JSONL startup and shutdown without Bluetooth hardware."""

import json
import subprocess
import sys


def main():
    result = subprocess.run(
        sys.argv[1:],
        input=json.dumps({"id": "smoke-close", "cmd": "close"}) + "\n",
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=60,
        check=True,
    )
    messages = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    if not messages or messages[0] != {"event": "ready"}:
        raise RuntimeError(f"Missing ready event: {messages!r}")
    if not any(message.get("id") == "smoke-close" and message.get("ok") is True for message in messages):
        raise RuntimeError(f"Missing successful close reply: {messages!r}")
    print("Bridge smoke passed: startup, JSONL command, clean exit")


if __name__ == "__main__":
    main()
