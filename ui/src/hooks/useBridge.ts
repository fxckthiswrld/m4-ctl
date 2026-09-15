import * as React from "react";
import type { BridgeCommand, BridgeEvent, BridgeReply, CommandResults } from "@/lib/bridge";

type Pending = { resolve: (reply: BridgeReply) => void; timer: number };

export function useBridge(onLog: (text: string) => void, onEvent: (event: BridgeEvent) => void) {
  const [ready, setReady] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [recovering, setRecovering] = React.useState(false);
  const pending = React.useRef(new Map<string, Pending>());
  const count = React.useRef(0);
  const sequence = React.useRef(0);
  const prefix = React.useRef(crypto.randomUUID());
  const callbacks = React.useRef({ onLog, onEvent });
  callbacks.current = { onLog, onEvent };

  const settleAll = React.useCallback((reply: BridgeReply) => {
    for (const item of pending.current.values()) {
      window.clearTimeout(item.timer);
      item.resolve(reply);
    }
    pending.current.clear();
  }, []);

  React.useEffect(() => {
    const api = window.m4;
    if (!api) return;
    let disposed = false;
    let lifecycleReceived = false;
    const handleEvent = (event: BridgeEvent) => {
      if (event.event === "ready") setReady(true);
      if (event.event === "starting") setReady(false);
      if (event.event === "reconnecting") setRecovering(true);
      if (event.event === "operation-complete") setRecovering(false);
      if (event.event === "stopped") {
        setReady(false);
        setRecovering(false);
        settleAll({ ok: false, error: event.error, cancelled: event.cancelled });
      }
      callbacks.current.onEvent(event);
    };
    const offReply = api.onReply((reply) => {
      const id = String(reply.id);
      const item = pending.current.get(id);
      if (!item) return;
      window.clearTimeout(item.timer);
      pending.current.delete(id);
      item.resolve(reply);
    });
    const offLog = api.onLog((line) => callbacks.current.onLog(line.trimEnd()));
    const offEvent = api.onEvent((event) => {
      if (["ready", "starting", "stopped"].includes(event.event)) lifecycleReceived = true;
      handleEvent(event);
    });
    void api.status().then((event) => {
      if (!disposed && !lifecycleReceived) handleEvent(event);
    }).catch(() => {});
    return () => {
      disposed = true;
      offReply(); offLog(); offEvent();
      settleAll({ ok: false, cancelled: true });
    };
  }, [settleAll]);

  const request = React.useCallback(<C extends BridgeCommand>(message: C, background = false): Promise<BridgeReply<CommandResults[C["cmd"]]>> => {
    const api = window.m4;
    if (!api) return Promise.resolve({ ok: false, error: "bridge unavailable" });
    const id = `${prefix.current}-${++sequence.current}`;
    if (!background) { count.current++; setBusy(true); }
    return new Promise((resolve) => {
      let settled = false;
      const finish = (reply: BridgeReply) => {
        if (settled) return;
        settled = true;
        if (!background) { count.current--; setBusy(count.current > 0); }
        // The command ID correlates this reply with its result type at the IPC boundary.
        resolve(reply as BridgeReply<CommandResults[C["cmd"]]>);
      };
      const timer = window.setTimeout(() => {
        if (!pending.current.delete(id)) return;
        finish({ ok: false, error: "timeout" });
        void api.cancel().catch(() => {});
      }, 65000);
      pending.current.set(id, { resolve: finish, timer });
      void Promise.resolve().then(() => api.cmd({ ...message, id })).catch((error: unknown) => {
        if (!pending.current.delete(id)) return;
        window.clearTimeout(timer);
        finish({ ok: false, error: error instanceof Error ? error.message : String(error) });
      });
    });
  }, []);

  const cancel = React.useCallback(async () => {
    settleAll({ ok: false, cancelled: true });
    setRecovering(false);
    await window.m4?.cancel();
  }, [settleAll]);

  return { request, cancel, ready, busy, recovering, hasPending: () => pending.current.size > 0 };
}
