# Momentum 4 GAIA3 Control

The command layouts below were checked against the Momentum 4 model definition
in Smart Control (`assets/app/m4.json`). They replace the old test fixtures that
used request IDs as response IDs and treated the last payload byte as a mode.
Hardware validation is still required for each supported firmware version.

| Setting | Write | Read | Read payload |
| --- | --- | --- | --- |
| ANC enabled | `1A04` | `1A05` | One byte: 0 or 1 |
| ANC modes | `1A00` | `1A01` | Three pairs: feature ID, value |
| Transparency | `1A02` | `1A03` | One byte: 0 to 100 |
| Transparent hearing enabled | `1804` | `1805` | One byte: 0 or 1 |

ANC feature 1 is Anti-Wind (0 off, 1 max, 2 auto), feature 2 is Comfort
(0 or 1), and feature 3 is Adaptive (0 or 1). Custom means Adaptive and
Comfort are disabled; it is not the Anti-Wind feature ID.

Success response IDs are the request ID with bit `0100` set. Error IDs have
bits `0180` set. The M4 model also lists `1982` and `1983` as transparency
errors, so the bridge accepts both forms as failures. Notifications use bit
`0080` and must not acknowledge pending writes. Successful writes have no
status prefix; failures are separate error packets.

The bridge matches vendor `0495` and the expected response ID, skips unrelated
frames, and routes replies through a single receive pump. The pump owns the
SPP channel while it is open: replies resolve per-command futures, device
errors reject them, and notifications (bit `0080`) are forwarded as bridge
events instead of being treated as data. A command timeout no longer closes
the channel; the pump simply drops the late reply so it can never acknowledge
a later command. Reads return null and a diagnostic error for an unavailable
field; no valid fields is an error. The UI reads state again after writes.

Electron assigns each request a 60-second deadline including queue time.
Python enforces the remaining deadline, checks it again between the steps of
multi-step commands (mode, custom, transparency, profile), and aborts the
sequence instead of writing past the deadline. Electron terminates an
unresponsive bridge after a further second. Cancelling an operation sends a
`cancel` command: the bridge aborts the in-flight request tasks and reports
them as cancelled while keeping the process and the SPP channel alive. The UI
has a 65-second fallback watchdog.

Channel loss and recovery are event-driven. The pump detects a dead channel,
fails pending commands with a connection error, emits `connection-lost`, and
starts a background recovery loop (attempts from 5 up to 30 seconds apart,
with jitter) when the session was not closed explicitly. While the channel is
open, a watchdog pings the device with a cheap state read after 25 seconds of
frame silence and tears the channel down when the ping fails. Explicit
`close` disables automatic recovery. Recovery emits `reconnecting` and ends
with `reconnected`; if it gives up within the recovery window it emits
`unreachable` and the UI surfaces the failure.

After every successful connection the bridge subscribes to device events with
the GAIA v3 registration command `0007` (response `0107`): feature `00`
(qcore, battery) and feature `02` (audio curation, ANC and transparency).
Registration failures are logged and ignored — not every firmware supports
every feature. Device events arrive as notification frames (bit `0080`) and
are forwarded to the renderer as `notification` events; the UI refreshes its
state read shortly after each notification instead of waiting for the next
poll cycle. The payload layouts of individual notifications are not decoded
yet and still require hardware verification. `tests/capture_notifications.py`
connects to a device and prints every raw notification for a given window,
which is how the payload layouts are being collected.

Bridge lifecycle events use a separate IPC channel. `ready` is queryable after
startup, and process exit releases pending requests immediately. Human-readable
transport logs go to stderr; stdout is reserved for JSON Lines. The bridge
logger also writes a rotated file (`m4bridge` logger, 1 MiB × 2 backups) when
Electron provides `M4_BRIDGE_LOG_FILE`; main keeps capped rings of bridge
events and stderr lines and merges them into the diagnostic export under the
`bridge` key.

## Profiles and Device Information

The `profile` command validates its entire input before writing, applies all
settings under the existing command lock, and reads them back. A mismatching
or incomplete readback is an error. Profiles do not apply automatically on
connection. Adaptive and Off profiles change the mode; Custom profiles also
apply Anti-Wind and transparency.

Optional `info` reads use the M4 model's battery command `0603` (response
`0703`) and firmware command `1202` (response `1302`). Battery values are
percentages; `FF` is unavailable. Firmware uses the model's byte-array version
representation joined with dots. These optional reads allow nine seconds per
response and return field errors without failing the ANC controls. They are
read on connection and periodically while connected.

The renderer owns the headphone controller even while its window is hidden.
Tray actions are routed to that controller, which prevents concurrent user
operations. The desktop process handles tray lifecycle, sleep events, and the
diagnostic save dialog. Auto-connect and close-to-tray default to off.
