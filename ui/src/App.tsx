import * as React from "react";
import {
  Headphones,
  VolumeX,
  Volume2,
  AudioWaveform,
  Wind,
  Zap,
  Check,
  RefreshCw,
  Power,
  PowerOff,
  Wifi,
  WifiOff,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { Select } from "@/components/ui/select";
import { Toggle } from "@/components/ui/toggle";
import { cn } from "@/lib/utils";
import type { BridgeReply, Device } from "@/lib/bridge";

type ConnectionStatus = "idle" | "connecting" | "connected" | "error";
type AmbientMode = "adaptive" | "custom" | "off";

const ANTIWIND_LEVELS = [
  { value: "0", label: "Off" },
  { value: "1", label: "Max" },
  { value: "2", label: "Auto" },
];

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-2xl border border-white/10 bg-card p-4 shadow-sm">
      <h2 className="mb-3 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
        {title}
      </h2>
      {children}
    </section>
  );
}

function RoundButton({
  icon,
  label,
  active,
  disabled,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  active?: boolean;
  disabled?: boolean;
  onClick?: () => void;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="flex flex-col items-center gap-2 disabled:opacity-40"
    >
      <span
        className={cn(
          "flex h-16 w-16 items-center justify-center rounded-full transition-colors",
          active
            ? "bg-primary text-white shadow-[0_0_20px_rgba(124,110,246,0.4)]"
            : "bg-secondary text-muted-foreground hover:bg-secondary/80"
        )}
      >
        {icon}
      </span>
      <span
        className={cn(
          "text-xs font-medium",
          active ? "text-primary" : "text-muted-foreground"
        )}
      >
        {label}
      </span>
    </button>
  );
}

export default function App() {
  const [bridgeReady, setBridgeReady] = React.useState(false);
  const [status, setStatus] = React.useState<ConnectionStatus>("idle");
  const [statusText, setStatusText] = React.useState("");
  const [devices, setDevices] = React.useState<Device[]>([]);
  const [selectedAddr, setSelectedAddr] = React.useState("");
  const [mode, setMode] = React.useState<AmbientMode>("adaptive");
  const [antiwind, setAntiwind] = React.useState("0");
  const [transparency, setTransparency] = React.useState(50);
  const [log, setLog] = React.useState<string[]>([]);
  const pendingRef = React.useRef<{ (m: BridgeReply): void } | null>(null);
  const logRef = React.useRef<string[]>([]);

  function pushLog(line: string) {
    logRef.current = [...logRef.current.slice(-200), line];
    setLog(logRef.current);
  }

  React.useEffect(() => {
    const w = window as any;
    if (!w.m4) return;
    setBridgeReady(true);
    w.m4.onReply((msg: BridgeReply) => {
      if (pendingRef.current) {
        const cb = pendingRef.current;
        pendingRef.current = null;
        cb(msg);
      }
    });
    w.m4.onLog((text: string) => {
      pushLog(text.replace(/\n$/, ""));
    });
    refreshDevices();
  }, []);

  function request(msg: any): Promise<BridgeReply> {
    const w = window as any;
    return new Promise((resolve) => {
      pendingRef.current = resolve;
      w.m4.cmd(msg);
      setTimeout(() => {
        if (pendingRef.current) {
          pendingRef.current = null;
          resolve({ ok: false, error: "timeout" });
        }
      }, 20000);
    });
  }

  async function refreshDevices() {
    pushLog("[ui] запрос списка устройств");
    const r = await request({ cmd: "list" });
    if (r.ok) {
      setDevices(r.result || []);
      pushLog(`[ui] найдено устройств: ${(r.result || []).length}`);
    } else {
      pushLog(`[ui] ошибка списка: ${r.error}`);
    }
  }

  async function connect() {
    if (!selectedAddr) return;
    setStatus("connecting");
    setStatusText(`Подключение к ${selectedAddr}...`);
    pushLog(`[ui] connect ${selectedAddr}`);
    const r = await request({ cmd: "connect", addr: selectedAddr });
    if (r.ok) {
      setStatus("connected");
      setStatusText("Подключено");
      pushLog("[ui] подключено");
      readState();
    } else {
      setStatus("error");
      setStatusText(`Ошибка: ${r.error}`);
      pushLog(`[ui] ошибка connect: ${r.error}`);
    }
  }

  async function disconnect() {
    setStatus("idle");
    setStatusText("");
    pushLog("[ui] close");
    await request({ cmd: "close" });
  }

  async function readState() {
    pushLog("[ui] чтение состояния");
    const r = await request({ cmd: "get" });
    if (r.ok) {
      const st = r.result?.state;
      pushLog(`[ui] состояние: ${JSON.stringify(st)}`);
    } else {
      pushLog(`[ui] ошибка get: ${r.error}`);
    }
  }

  async function setAmbientMode(m: AmbientMode) {
    setMode(m);
    if (status !== "connected") {
      pushLog("[ui] не подключено, команда не отправлена");
      return;
    }
    if (m === "custom") {
      pushLog("[ui] custom");
      const r = await request({ cmd: "custom" });
      pushLog(r.ok ? "[ui] custom ок" : `[ui] custom: ${r.error}`);
    } else if (m === "adaptive") {
      pushLog("[ui] mode adaptive");
      const r = await request({ cmd: "mode", mode: "adaptive" });
      pushLog(r.ok ? "[ui] adaptive ок" : `[ui] adaptive: ${r.error}`);
    } else {
      pushLog("[ui] anc off");
      const r = await request({ cmd: "anc", state: "off" });
      pushLog(r.ok ? "[ui] anc off ок" : `[ui] anc off: ${r.error}`);
    }
  }

  async function setAntiwindLevel(v: string) {
    setAntiwind(v);
    if (status !== "connected") return;
    pushLog(`[ui] antiwind ${v}`);
    const r = await request({ cmd: "antiwind", level: parseInt(v, 10) });
    pushLog(r.ok ? `[ui] antiwind ${v} ок` : `[ui] antiwind: ${r.error}`);
  }

  async function setTransparencyLevel(v: number) {
    setTransparency(v);
    if (status !== "connected") return;
    pushLog(`[ui] transparency ${v}`);
    const r = await request({ cmd: "transparency", level: v });
    pushLog(r.ok ? `[ui] transparency ${v} ок` : `[ui] transparency: ${r.error}`);
  }

  const connected = status === "connected";

  return (
    <div className="mx-auto flex min-h-screen w-full max-w-md flex-col gap-3 bg-background px-4 py-5">
      {/* Hero */}
      <div className="relative flex flex-col items-center gap-1 rounded-2xl border border-white/10 bg-gradient-to-b from-card to-card/40 px-4 py-6">
        <div className="pointer-events-none absolute inset-x-0 top-0 h-32 rounded-t-2xl bg-primary/15 blur-3xl" />
        <div className="relative flex h-24 w-24 items-center justify-center rounded-full bg-secondary/60">
          <Headphones className="h-12 w-12 text-primary" />
        </div>
        <h1 className="relative mt-2 text-lg font-bold">MOMENTUM 4</h1>
        <p className="relative flex items-center gap-1 text-xs text-muted-foreground">
          {connected ? (
            <>
              <Wifi className="h-3.5 w-3.5 text-emerald-400" />
              Подключено
            </>
          ) : (
            <>
              <WifiOff className="h-3.5 w-3.5 text-muted-foreground" />
              {status === "connecting"
                ? "Подключение..."
                : status === "error"
                  ? statusText
                  : "Не подключено"}
            </>
          )}
        </p>
      </div>

      {/* Устройство */}
      <Section title="Устройство">
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <Select
              value={selectedAddr}
              options={[
                { value: "", label: "Выберите наушники..." },
                ...devices.map((d) => ({
                  value: d.address,
                  label: `${d.name} (${d.address})`,
                })),
              ]}
              onChange={setSelectedAddr}
              className="flex-1"
            />
            <Button
              size="icon"
              variant="outline"
              onClick={refreshDevices}
              title="Обновить список"
            >
              <RefreshCw className="h-4 w-4" />
            </Button>
          </div>
          <div className="flex gap-2">
            <Button
              variant="outline"
              className="flex-1"
              disabled={!selectedAddr || status === "connecting"}
              onClick={connect}
            >
              <Power className="h-4 w-4" />
              Подключить
            </Button>
            <Button
              variant="outline"
              className="flex-1"
              disabled={!connected}
              onClick={disconnect}
            >
              <PowerOff className="h-4 w-4" />
              Отключить
            </Button>
          </div>
        </div>
      </Section>

      {/* Ambient Sound Control */}
      <Section title="Ambient Sound Control">
        <div className="flex items-start justify-around">
          <RoundButton
            icon={<AudioWaveform className="h-6 w-6" />}
            label="Adaptive"
            active={mode === "adaptive"}
            disabled={!connected}
            onClick={() => setAmbientMode("adaptive")}
          />
          <RoundButton
            icon={<Volume2 className="h-6 w-6" />}
            label="Custom"
            active={mode === "custom"}
            disabled={!connected}
            onClick={() => setAmbientMode("custom")}
          />
          <RoundButton
            icon={<VolumeX className="h-6 w-6" />}
            label="Off"
            active={mode === "off"}
            disabled={!connected}
            onClick={() => setAmbientMode("off")}
          />
        </div>
      </Section>

      {/* Anti-Wind */}
      <Section title="Anti-Wind">
        <div className="grid grid-cols-3 gap-2">
          {ANTIWIND_LEVELS.map((l) => (
            <Button
              key={l.value}
              variant={antiwind === l.value ? "default" : "secondary"}
              disabled={!connected}
              onClick={() => setAntiwindLevel(l.value)}
              className={cn(
                "rounded-full",
                antiwind === l.value
                  ? "bg-primary text-primary-foreground"
                  : "bg-secondary text-muted-foreground"
              )}
            >
              {l.value === "0" ? <Wind className="h-4 w-4" /> : <Zap className="h-4 w-4" />}
              {l.label}
            </Button>
          ))}
        </div>
      </Section>

      {/* Transparency */}
      <Section title="Прозрачность (Кастом)">
        <div className="flex items-center gap-3">
          <Slider
            value={transparency}
            min={0}
            max={100}
            step={5}
            disabled={!connected}
            onChange={setTransparencyLevel}
          />
          <span className="w-10 text-right text-sm font-semibold tabular-nums">
            {transparency}
          </span>
        </div>
      </Section>

      {/* Лог */}
      <Section title="Лог">
        <div className="max-h-44 overflow-y-auto rounded-lg bg-black/40 p-2 font-mono text-[11px] leading-relaxed text-muted-foreground">
          {log.length === 0 ? (
            <span className="text-muted-foreground/50">Пусто</span>
          ) : (
            log.map((line, i) => <div key={i}>{line}</div>)
          )}
        </div>
      </Section>

      {!bridgeReady && (
        <p className="text-center text-xs text-red-400">
          Мост не доступен — запустите через Electron (npm run dev)
        </p>
      )}
    </div>
  );
}