import * as React from "react";
import { useBridge } from "./useBridge";
import type { AmbientMode, BridgeCommand, BridgeEvent, ConnectionStatus, DeviceInfo, DeviceState, ProfileSettings } from "@/lib/bridge";
import type { Translations } from "@/lib/translations";

export function useHeadphones(t: Translations) {
  const [status, updateStatus] = React.useState<ConnectionStatus>("idle");
  const statusRef = React.useRef(status);
  const [statusText, setStatusText] = React.useState("");
  const [deviceState, setDeviceState] = React.useState<DeviceState | null>(null);
  const [info, setInfo] = React.useState<DeviceInfo | null>(null);
  const [mode, setMode] = React.useState<AmbientMode | "comfort" | null>(null);
  const [antiwind, setAntiwind] = React.useState<string | null>(null);
  const [transparency, setTransparency] = React.useState(0);
  const [log, setLog] = React.useState<string[]>([]);
  const [working, setWorking] = React.useState(false);
  const operation = React.useRef(false);
  const revision = React.useRef(0);
  const session = React.useRef(0);
  const adjusting = React.useRef(false);
  const notificationTimer = React.useRef(0);
  const suspended = React.useRef(false);
  const lastAddress = React.useRef(localStorage.getItem("m4-last-connected") || "");
  // The headphones do not keep the Custom slider across mode switches (the
  // official app restores it), so the app stores the last confirmed value.
  const [customLevel, setCustomLevel] = React.useState<number | null>(() => {
    const saved = Number(localStorage.getItem("m4-custom-level"));
    return Number.isInteger(saved) && saved >= 0 && saved <= 100 ? saved : null;
  });
  const retryAttempt = React.useRef(0);

  const setStatus = (value: ConnectionStatus) => { statusRef.current = value; updateStatus(value); };
  const pushLog = (line: string) => setLog((previous) => [...previous.slice(-199), `${new Date().toISOString()} ${line.slice(0, 4096)}`]);
  function clearState() {
    revision.current++;
    adjusting.current = false;
    window.clearTimeout(notificationTimer.current);
    setDeviceState(null); setMode(null); setAntiwind(null); setTransparency(0);
  }
  function reset() {
    session.current++;
    operation.current = false;
    setWorking(false);
    clearState(); setInfo(null);
  }
  function applyState(state: DeviceState) {
    setDeviceState(state);
    setMode(state.anc?.enabled === false ? "off" : state.mode?.key || null);
    const wind = state.mode?.antiwind;
    setAntiwind(typeof wind === "number" && [0, 1, 2].includes(wind) ? String(wind) : null);
    setTransparency(state.transparency?.level ?? 0);
    // Remember the slider as reported while Custom is active: switching modes
    // back later must restore exactly this value (like the official app).
    const level = state.transparency?.level;
    if (state.mode?.key === "custom" && typeof level === "number") {
      setCustomLevel((previous) => {
        if (previous === level) return previous;
        localStorage.setItem("m4-custom-level", String(level));
        return level;
      });
    }
  }
  function failed(error = "device state unavailable") {
    setStatus("error"); setStatusText(error); clearState();
    pushLog(error);
  }
  function onEvent(event: BridgeEvent) {
    // The headphones push the new ANC mode when a gesture changes it: show it
    // immediately; the debounced state read (the raw notification event)
    // confirms the full state shortly after.
    if (event.event === "device-mode" && typeof event.mode === "string") {
      const mapped = event.mode === "anti_wind" ? "custom" : event.mode;
      if (["adaptive", "custom", "off", "comfort"].includes(mapped)) {
        setMode(mapped as AmbientMode | "comfort");
      }
      return;
    }
    // Device-side changes (ANC button, wear detection) arrive as GAIA
    // notifications: refresh the state shortly after instead of waiting for
    // the poll cycle. The background guard in readState skips collisions.
    if (event.event === "notification") {
      window.clearTimeout(notificationTimer.current);
      notificationTimer.current = window.setTimeout(() => {
        void actions.current?.readState(true);
      }, 300);
      return;
    }
    // The bridge gave up restoring a self-dropped session.
    if (event.event === "unreachable") {
      if (suspended.current) return;
      reset();
      setStatus("error");
      setStatusText(t.unreachable);
      pushLog(t.unreachable);
      return;
    }
    if (event.event !== "stopped") return;
    window.clearTimeout(notificationTimer.current);
    reset();
    setStatus(event.cancelled ? "idle" : "error");
    setStatusText(event.cancelled ? "" : event.error || "bridge unavailable");
  }
  const bridge = useBridge(pushLog, onEvent);
  const begin = () => { operation.current = true; setWorking(true); revision.current++; };
  const end = (generation: number) => {
    if (generation === session.current) { operation.current = false; setWorking(false); }
  };

  async function readInfo() {
    const generation = session.current;
    const result = await bridge.request({ cmd: "info" }, true);
    if (generation !== session.current || result.cancelled) return;
    if (result.ok) setInfo(result.result);
    else { setInfo(null); pushLog(result.error || "device info unavailable"); }
  }

  async function readState(background = false) {
    if (background && (bridge.hasPending() || adjusting.current || operation.current || suspended.current)) return;
    if (!background) { revision.current++; pushLog(t.readingState); }
    const snapshot = revision.current;
    const result = await bridge.request({ cmd: "get" }, background);
    if (snapshot !== revision.current || result.cancelled) return;
    if (result.ok && result.result.state && Object.values(result.result.state).some((value) => value != null)) {
      applyState(result.result.state);
      if (!background) pushLog(t.state(JSON.stringify(result.result.state)));
    } else failed(result.ok ? undefined : result.error);
  }

  async function connect() {
    // Авто-подключение: пустой адрес = бридж сам находит сопряжённые
    // наушники Sennheiser, явный выбор устройства не нужен.
    if (operation.current || suspended.current) return;
    reset(); begin();
    const generation = session.current;
    setStatus("connecting"); setStatusText("");
    pushLog(lastAddress.current ? t.connectingTo(lastAddress.current) : t.connectingAuto);
    const result = await bridge.request({ cmd: "connect", addr: lastAddress.current });
    if (result.cancelled || generation !== session.current) return;
    if (result.ok) {
      setStatus("connected");
      lastAddress.current = result.result.addr;
      localStorage.setItem("m4-last-connected", result.result.addr);
      pushLog(t.connectedLog);
      await readState();
      if (generation === session.current && statusRef.current === "connected") {
        retryAttempt.current = 0;
        void readInfo();
      }
    } else failed(result.error);
    end(generation);
  }

  async function cancel() {
    reset(); setStatus("idle"); setStatusText("");
    await bridge.cancel();
  }

  async function change(command: BridgeCommand, preview?: () => void) {
    if (statusRef.current !== "connected" || operation.current || bridge.recovering || suspended.current) return;
    begin(); preview?.();
    const generation = session.current;
    pushLog(t.command(JSON.stringify(command)));
    const result = await bridge.request(command);
    if (result.cancelled || generation !== session.current) return;
    if (!result.ok) failed(result.error);
    else {
      pushLog(t.commandOk(command.cmd));
      if (command.cmd === "profile" && "state" in result.result) applyState(result.result.state);
      else await readState();
    }
    end(generation);
  }

  const setAmbientMode = async (value: AmbientMode) => {
    // No-op: the target mode is already active (device state is tracked via
    // notifications and polls, so the UI mode matches the headphones).
    if (mode === value) return;
    if (value === "custom") {
      // Restore the last Custom slider value; neutral (no ANC, no
      // transparency) until the user picks a value.
      await change({ cmd: "custom", level: customLevel ?? 50 }, () => setMode(value));
      return;
    }
    await change(value === "off" ? { cmd: "anc", state: "off" } : { cmd: "mode", mode: "adaptive" }, () => setMode(value));
  };
  const setAntiwindLevel = async (value: string) => {
    if (antiwind === value) return;
    await change({ cmd: "antiwind", level: Number(value) }, () => setAntiwind(value));
  };
  const commitTransparencyLevel = (value: number) => change({ cmd: "transparency", level: value }, () => setTransparency(value));
  const applyProfile = (profile: ProfileSettings) => change({ cmd: "profile", mode: profile.mode, antiwind: profile.antiwind, transparency: profile.transparency });
  const onInteractionChange = (value: boolean) => { adjusting.current = value; if (value) revision.current++; };

  const actions = React.useRef({ connect, readState, readInfo });
  actions.current = { connect, readState, readInfo };
  React.useEffect(() => {
    // Авто-подключение при старте: с сохранённым адресом или авто-поиском.
    void actions.current.connect();
    return () => { session.current++; };
  }, []);

  React.useEffect(() => {
    if (status !== "connected") return;
    let polls = 0;
    const timer = window.setInterval(() => {
      void actions.current.readState(true).then(() => {
        if (++polls % 6 === 0 && !bridge.hasPending() && statusRef.current === "connected") void actions.current.readInfo();
      });
    }, 10000);
    return () => window.clearInterval(timer);
  }, [status]);

  React.useEffect(() => {
    if (status !== "error" || suspended.current || retryAttempt.current >= 3) return;
    const timer = window.setTimeout(() => {
      void actions.current.connect();
    }, Math.min(15000 * 2 ** retryAttempt.current++, 60000));
    return () => window.clearTimeout(timer);
  }, [status]);

  async function suspend() { suspended.current = true; await cancel(); }
  function resume() {
    suspended.current = false;
    retryAttempt.current = 0;
    if (statusRef.current !== "connected") void connect();
  }

  const connected = status === "connected";
  const busy = working || bridge.busy;
  return { status, statusText, deviceState, info, mode, antiwind,
    transparency, setTransparency, log, pushLog, bridgeReady: bridge.ready, recovering: bridge.recovering,
    busy, connected, customControlsDisabled: !connected || busy || bridge.recovering || mode !== "custom",
    setAmbientMode, setAntiwindLevel, commitTransparencyLevel, onInteractionChange, applyProfile, suspend, resume };
}
