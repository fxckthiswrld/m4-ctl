export type AmbientMode = "adaptive" | "custom" | "off";
export type ConnectionStatus = "idle" | "connecting" | "connected" | "error";

export interface Device { name: string; address: string }
export interface DeviceState {
  anc?: { enabled: boolean; raw?: string } | null;
  mode?: { key: AmbientMode | "comfort"; name?: string; antiwind: number; raw?: string } | null;
  transparency?: { level: number; raw?: string } | null;
  transparent_hearing?: { enabled: boolean; raw?: string } | null;
}
export interface StateResult { state: DeviceState; errors?: Record<string, string> }
export interface DeviceInfo { battery: number[] | null; firmware: string | null; errors?: Record<string, string> }
export interface ProfileSettings { mode: AmbientMode; antiwind: number; transparency: number }
export interface Profile extends ProfileSettings { id: string; name: string }

export type BridgeCommand =
  | { cmd: "list" }
  | { cmd: "connect"; addr: string }
  | { cmd: "close" }
  | { cmd: "get" }
  | { cmd: "info" }
  | { cmd: "anc"; state: "on" | "off" }
  | { cmd: "mode"; mode: "adaptive" | "comfort" | "anti_wind" | "off" }
  | { cmd: "custom" }
  | { cmd: "antiwind"; level: number }
  | { cmd: "transparency"; level: number }
  | ({ cmd: "profile" } & ProfileSettings);

export interface CommandResults {
  list: Device[];
  connect: { connected: boolean; addr: string };
  close: { closed: boolean };
  get: StateResult;
  info: DeviceInfo;
  anc: { anc: string };
  mode: { mode: string };
  custom: { mode: string };
  antiwind: { antiwind: number };
  transparency: { transparency: number };
  profile: StateResult;
}
export type BridgeReply<T = unknown> = { id?: string | number } & (
  | { ok: true; result: T; cancelled?: false }
  | { ok: false; error?: string; cancelled?: boolean }
);
export interface BridgeEvent {
  event: "starting" | "ready" | "stopped" | "reconnecting" | "operation-complete";
  error?: string;
  cancelled?: boolean;
}
export interface DesktopState {
  language: "en" | "ru";
  connected: boolean;
  busy: boolean;
  canConnect: boolean;
  closeToTray: boolean;
  mode: AmbientMode | "comfort" | null;
  profiles: { id: string; name: string }[];
}
export type DesktopAction =
  | { action: "connect" | "disconnect" | "suspend" | "resume" }
  | { action: "mode"; mode: AmbientMode }
  | { action: "profile"; id: string };
export interface Desktop {
  sync: (state: DesktopState) => Promise<void>;
  onAction: (callback: (action: DesktopAction) => void) => () => void;
  exportDiagnostics: (snapshot: { logs: string[]; state: DeviceState | null; info: DeviceInfo | null }) => Promise<{ saved: boolean }>;
}
export interface Bridge {
  cmd: (message: BridgeCommand & { id: string }) => Promise<{ queued: boolean }>;
  status: () => Promise<BridgeEvent>;
  cancel: () => Promise<void>;
  onEvent: (callback: (event: BridgeEvent) => void) => () => void;
  onReply: (callback: (reply: BridgeReply) => void) => () => void;
  onLog: (callback: (text: string) => void) => () => void;
}
declare global {
  interface Window { m4?: Bridge; desktop?: Desktop }
}
