"""Live probe: does the M4 store the Custom slider value on device?

Drives bridge.py over stdio and answers:
  T0 current transparency -> switch to Custom (no slider write) -> T1
  set slider 63 -> T2 -> switch to Adaptive -> T3 -> switch back to Custom
  (no slider write) -> T4

If T4 == 63 the headphones keep the Custom value themselves; if it drifts
to a default, the official app is the one restoring it.

Run with the headphones connected to this computer and the Sennheiser app
disconnected:  uv run python tests/probe_custom_mode.py
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


def transparency(result):
    level = (result.get("state") or {}).get("transparency") or {}
    mode = (result.get("state") or {}).get("mode") or {}
    return level.get("level"), mode.get("key"), (result.get("state") or {}).get("anc")


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
    level, mode, anc = transparency(send({"cmd": "get"}))
    print(f"T0 start: transparency={level} mode={mode} anc={anc}")

    send({"cmd": "custom"})
    time.sleep(1)
    level, mode, anc = transparency(send({"cmd": "get"}))
    print(f"T1 after custom (no slider write): transparency={level} mode={mode} anc={anc}")

    send({"cmd": "transparency", "level": 63})
    time.sleep(1)
    level, mode, anc = transparency(send({"cmd": "get"}))
    print(f"T2 after set 63: transparency={level} mode={mode} anc={anc}")

    send({"cmd": "mode", "mode": "adaptive"})
    time.sleep(1)
    level, mode, anc = transparency(send({"cmd": "get"}))
    print(f"T3 after adaptive: transparency={level} mode={mode} anc={anc}")

    send({"cmd": "custom"})
    time.sleep(1)
    level, mode, anc = transparency(send({"cmd": "get"}))
    print(f"T4 after custom again (no slider write): transparency={level} mode={mode} anc={anc}")

    if level == 63:
        print("\nVERDICT: the HEADPHONES store the Custom slider value.")
    else:
        print(f"\nVERDICT: the slider did not survive (got {level}) - the OFFICIAL APP restores it.")
    send({"cmd": "close"})
    proc.terminate()


if __name__ == "__main__":
    main()
