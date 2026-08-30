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
