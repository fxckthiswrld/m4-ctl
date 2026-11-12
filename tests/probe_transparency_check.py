"""Live check: transparency engagement with the new custom(level) flow.

Repeats the user's scenario: enter Custom (app restores level 0 = full ANC),
then drag the slider to the transparency side and read back what the device
reports.  uv run python tests/probe_transparency_check.py
"""

import json
import subprocess
import sys
import threading
import time

replies = {}
lock = threading.Lock()


def reader(proc):
    for line in proc.stdout:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if "id" in message:
            with lock:
                replies[message["id"]] = message
        else:
            print(f"[event] {message.get('event')}")


def request(proc, next_id, payload, timeout=30):
    proc.stdin.write(json.dumps({"id": next_id, **payload}) + "\n")
    proc.stdin.flush()
    deadline = time.time() + timeout
    while time.time() < deadline:
        with lock:
            reply = replies.pop(next_id, None)
        if reply is not None:
            if not reply.get("ok"):
                raise RuntimeError(f"{payload.get('cmd')} failed: {reply.get('error')}")
            return reply["result"]
        time.sleep(0.05)
    raise TimeoutError(f"{payload.get('cmd')} timed out")


def snapshot(result):
    state = result.get("state") or {}
    return (
        state.get("transparency", {}).get("level") if state.get("transparency") else None,
        state.get("mode", {}).get("key") if state.get("mode") else None,
        state.get("anc", {}).get("enabled") if state.get("anc") else None,
    )


def main():
    proc = subprocess.Popen(
        [sys.executable, "bridge.py"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", cwd=".",
    )
    threading.Thread(target=reader, args=(proc,), daemon=True).start()
    next_id = 1

    def send(payload, timeout=30):
        nonlocal next_id
        next_id += 1
        return request(proc, next_id, payload, timeout)

    print("connect:", send({"cmd": "connect"}))
    time.sleep(1)
    print("S0:", snapshot(send({"cmd": "get"})))

    send({"cmd": "custom", "level": 0})
    time.sleep(1)
    print("S1 custom level=0:", snapshot(send({"cmd": "get"})))

    send({"cmd": "transparency", "level": 80})
    time.sleep(1)
    print("S2 transparency=80:", snapshot(send({"cmd": "get"})))

    send({"cmd": "mode", "mode": "adaptive"})
    time.sleep(1)
    send({"cmd": "custom", "level": 0})
    time.sleep(1)
    print("S3 adaptive->custom level=0:", snapshot(send({"cmd": "get"})))

    send({"cmd": "transparency", "level": 100})
    time.sleep(1)
    print("S4 transparency=100:", snapshot(send({"cmd": "get"})))

    send({"cmd": "close"})
    proc.terminate()


if __name__ == "__main__":
    main()
