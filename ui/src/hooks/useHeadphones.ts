import * as React from "react";
import { useBridge } from "./useBridge";
import type { AmbientMode, BridgeCommand, BridgeEvent, ConnectionStatus, Device, DeviceInfo, DeviceState, ProfileSettings } from "@/lib/bridge";
import type { Translations } from "@/lib/translations";

export function useHeadphones(t: Translations, autoConnect: boolean) {
  const [status, updateStatus] = React.useState<ConnectionStatus>("idle");
  const statusRef = React.useRef(status);
  const [statusText, setStatusText] = React.useState("");
  const [devices, setDevices] = React.useState<Device[]>([]);
  const [deviceListStatus, setDeviceListStatus] = React.useState<"loading" | "ready" | "error">("loading");
  const [deviceListError, setDeviceListError] = React.useState("");
  const listing = React.useRef(false);
  const [selectedAddr, setSelectedAddr] = React.useState(() => localStorage.getItem("m4-device-address") || "");
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
  const manuallyStopped = React.useRef(sessionStorage.getItem("m4-manually-stopped") === "true");
  const suspended = React.useRef(false);
  const lastAddress = React.useRef(localStorage.getItem("m4-last-connected") || "");
  const retryAttempt = React.useRef(0);
  const latest = React.useRef({ autoConnect, selectedAddr });
  const previousAutoConnect = React.useRef(autoConnect);
  latest.current = { autoConnect, selectedAddr };

  const setStatus = (value: ConnectionStatus) => { statusRef.current = value; updateStatus(value); };
  const setManuallyStopped = (value: boolean) => {
    manuallyStopped.current = value;
    sessionStorage.setItem("m4-manually-stopped", String(value));
  };
  const pushLog = (line: string) => setLog((previous) => [...previous.slice(-199), `${new Date().toISOString()} ${line.slice(0, 4096)}`]);
  function clearState() {
    revision.current++;
    adjusting.current = false;
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
  }
  function failed(error = "device state unavailable") {
    setStatus("error"); setStatusText(error); clearState();
    pushLog(error);
  }
  function onEvent(event: BridgeEvent) {
    if (event.event !== "stopped") return;
    reset();
    setStatus(event.cancelled ? "idle" : "error");
    setStatusText(event.cancelled ? "" : event.error || "bridge unavailable");
  }
  const bridge = useBridge(pushLog, onEvent);
  const begin = () => { operation.current = true; setWorking(true); revision.current++; };
  const end = (generation: number) => {
    if (generation === session.current) { operation.current = false; setWorking(false); }
  };

  React.useEffect(() => {
    if (selectedAddr) localStorage.setItem("m4-device-address", selectedAddr);
    else localStorage.removeItem("m4-device-address");
  }, [selectedAddr]);

  async function refreshDevices() {
    if (listing.current) return;
    listing.current = true;
    setDeviceListStatus("loading"); setDeviceListError("");
    pushLog(t.requestingDevices);
    const result = await bridge.request({ cmd: "list" });
    listing.current = false;
    if (result.cancelled) { setDeviceListStatus("ready"); return; }
    if (result.ok) {
      setDevices(result.result); setDeviceListStatus("ready");
      pushLog(t.foundDevices(result.result.length));
    } else {
      setDeviceListStatus("error"); setDeviceListError(result.error || "unknown");
      pushLog(t.deviceListError(result.error || "unknown"));
    }
  }

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

  async function connect(address = selectedAddr, automatic = false) {
    if (!address || operation.current || suspended.current) return;
    if (automatic && manuallyStopped.current) return;
    setManuallyStopped(false);
    reset(); begin();
    const generation = session.current;
    setStatus("connecting"); setStatusText("");
    pushLog(t.connectingTo(address));
    const result = await bridge.request({ cmd: "connect", addr: address });
    if (result.cancelled || generation !== session.current) return;
    if (result.ok) {
      setStatus("connected"); setSelectedAddr(address);
      lastAddress.current = address;
      localStorage.setItem("m4-last-connected", address);
      retryAttempt.current = 0;
      pushLog(t.connectedLog);
      await readState();
      if (generation === session.current && statusRef.current === "connected") void readInfo();
    } else failed(result.error);
    end(generation);
  }

  async function cancel(manual = true) {
    if (manual) setManuallyStopped(true);
    reset(); setStatus("idle"); setStatusText("");
    await bridge.cancel();
  }

  async function disconnect() {
    setManuallyStopped(true);
    reset(); begin();
    const generation = session.current;
    setStatus("idle"); setStatusText(""); pushLog(t.closing);
    const result = await bridge.request({ cmd: "close" });
    if (!result.ok && !result.cancelled && generation === session.current) failed(result.error);
    end(generation);
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

  const setAmbientMode = (value: AmbientMode) => change(value === "custom" ? { cmd: "custom" } : value === "off" ? { cmd: "anc", state: "off" } : { cmd: "mode", mode: "adaptive" }, () => setMode(value));
  const setAntiwindLevel = (value: string) => change({ cmd: "antiwind", level: Number(value) }, () => setAntiwind(value));
  const commitTransparencyLevel = (value: number) => change({ cmd: "transparency", level: value }, () => setTransparency(value));
  const applyProfile = (profile: ProfileSettings) => change({ cmd: "profile", mode: profile.mode, antiwind: profile.antiwind, transparency: profile.transparency });
  const onInteractionChange = (value: boolean) => { adjusting.current = value; if (value) revision.current++; };

  const actions = React.useRef({ connect, readState, readInfo });
  actions.current = { connect, readState, readInfo };
  React.useEffect(() => {
    let disposed = false;
    void refreshDevices().then(() => {
      if (!disposed && latest.current.autoConnect && lastAddress.current) void actions.current.connect(lastAddress.current, true);
    });
    return () => { disposed = true; session.current++; };
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
    if (!autoConnect || status !== "error" || manuallyStopped.current || suspended.current) return;
    const address = selectedAddr || lastAddress.current;
    if (!address) return;
    const timer = window.setTimeout(() => {
      void actions.current.connect(address, true);
    }, Math.min(15000 * 2 ** retryAttempt.current++, 60000));
    return () => window.clearTimeout(timer);
  }, [autoConnect, status, selectedAddr]);

  React.useEffect(() => {
    if (autoConnect && !previousAutoConnect.current) {
      setManuallyStopped(false);
      if (statusRef.current !== "connected") void actions.current.connect(selectedAddr || lastAddress.current, true);
    }
    previousAutoConnect.current = autoConnect;
  }, [autoConnect, selectedAddr]);

  async function suspend() { suspended.current = true; await cancel(false); }
  function resume() {
    suspended.current = false;
    if (latest.current.autoConnect && !manuallyStopped.current && lastAddress.current) void connect(lastAddress.current, true);
  }

  const connected = status === "connected";
  const busy = working || bridge.busy;
  return { status, statusText, devices, deviceListStatus, deviceListError, selectedAddr, setSelectedAddr, deviceState, info, mode, antiwind,
    transparency, setTransparency, log, pushLog, bridgeReady: bridge.ready, recovering: bridge.recovering,
    busy, connected, customControlsDisabled: !connected || busy || bridge.recovering || mode !== "custom",
    refreshDevices, connect: () => connect(), disconnect, cancel: () => cancel(), setAmbientMode,
    setAntiwindLevel, commitTransparencyLevel, onInteractionChange, applyProfile, suspend, resume };
}
