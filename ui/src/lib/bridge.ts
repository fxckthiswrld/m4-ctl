export interface Device {
  name: string;
  address: string;
}

export interface BridgeReply {
  id?: string | number;
  ok: boolean;
  result?: any;
  error?: string;
}

export interface DeviceState {
  anc?: { enabled?: boolean; raw?: string } | null;
  mode?: { key?: string; name?: string; code?: number; raw?: string } | null;
  transparency?: { level?: number; raw?: string } | null;
  transparent_hearing?: { enabled?: boolean; raw?: string } | null;
}

export interface Bridge {
  cmd: (msg: any) => Promise<any>;
  onReply: (cb: (msg: BridgeReply) => void) => () => void;
  onLog: (cb: (text: string) => void) => () => void;
}

declare global {
  interface Window {
    m4?: Bridge;
  }
}
