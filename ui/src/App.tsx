import * as React from "react";
import {
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
import headphonesImage from "../../headphones_nobg.png";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { Select } from "@/components/ui/select";
import { Toggle } from "@/components/ui/toggle";
import { cn } from "@/lib/utils";
import type { BridgeReply, Device } from "@/lib/bridge";

type ConnectionStatus = "idle" | "connecting" | "connected" | "error";
type AmbientMode = "adaptive" | "custom" | "off";
type Language = "en" | "ru";

const translations = {
  en: {
    language: "Language",
    english: "English",
    russian: "Russian",
    connected: "Connected",
    connecting: "Connecting...",
    disconnected: "Not connected",
    error: "Error",
    device: "Device",
    chooseHeadphones: "Choose headphones...",
    refreshDevices: "Refresh device list",
    connect: "Connect",
    disconnect: "Disconnect",
    ambient: "Ambient Sound Control",
    adaptive: "Adaptive",
    custom: "Custom",
    off: "Off",
    antiWind: "Anti-Wind",
    max: "Max",
    auto: "Auto",
    transparency: "Transparency (Custom)",
    log: "Log",
    empty: "Empty",
    bridgeUnavailable: "Bridge unavailable - launch through Electron (npm run dev)",
    requestingDevices: "[ui] requesting device list",
    foundDevices: (count: number) => `[ui] found devices: ${count}`,
    deviceListError: (error: string) => `[ui] device list error: ${error}`,
    connectingTo: (address: string) => `[ui] connect ${address}`,
    connectedLog: "[ui] connected",
    connectionError: (error: string) => `[ui] connect error: ${error}`,
    closing: "[ui] close",
    readingState: "[ui] reading state",
    state: (value: string) => `[ui] state: ${value}`,
    getError: (error: string) => `[ui] get error: ${error}`,
    notConnected: "[ui] not connected, command not sent",
    command: (value: string) => `[ui] ${value}`,
    commandOk: (value: string) => `[ui] ${value} ok`,
    commandError: (value: string, error: string) => `[ui] ${value}: ${error}`,
  },
  ru: {
    language: "Язык",
    english: "English",
    russian: "Русский",
    connected: "Подключено",
    connecting: "Подключение...",
    disconnected: "Не подключено",
    error: "Ошибка",
    device: "Устройство",
    chooseHeadphones: "Выберите наушники...",
    refreshDevices: "Обновить список устройств",
    connect: "Подключить",
    disconnect: "Отключить",
    ambient: "Управление окружающим звуком",
    adaptive: "Адаптивный",
    custom: "Настраиваемый",
    off: "Выкл.",
    antiWind: "Защита от ветра",
    max: "Макс.",
    auto: "Авто",
    transparency: "Прозрачность (настройка)",
    log: "Лог",
    empty: "Пусто",
    bridgeUnavailable: "Мост недоступен - запустите через Electron (npm run dev)",
    requestingDevices: "[ui] запрос списка устройств",
    foundDevices: (count: number) => `[ui] найдено устройств: ${count}`,
    deviceListError: (error: string) => `[ui] ошибка списка: ${error}`,
    connectingTo: (address: string) => `[ui] connect ${address}`,
    connectedLog: "[ui] подключено",
    connectionError: (error: string) => `[ui] ошибка connect: ${error}`,
    closing: "[ui] close",
    readingState: "[ui] чтение состояния",
    state: (value: string) => `[ui] состояние: ${value}`,
    getError: (error: string) => `[ui] ошибка get: ${error}`,
    notConnected: "[ui] не подключено, команда не отправлена",
    command: (value: string) => `[ui] ${value}`,
    commandOk: (value: string) => `[ui] ${value} ок`,
    commandError: (value: string, error: string) => `[ui] ${value}: ${error}`,
  },
} as const;

const ANTIWIND_LEVELS = [
  { value: "0", key: "off" },
  { value: "1", key: "max" },
  { value: "2", key: "auto" },
] as const;

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
  const [language, setLanguage] = React.useState<Language>(() => {
    const saved = window.localStorage.getItem("m4-language");
    if (saved === "en" || saved === "ru") return saved;
    return navigator.language.toLowerCase().startsWith("ru") ? "ru" : "en";
  });
  const t = translations[language];
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

  React.useEffect(() => {
    window.localStorage.setItem("m4-language", language);
    document.documentElement.lang = language;
  }, [language]);

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
    pushLog(t.requestingDevices);
    const r = await request({ cmd: "list" });
    if (r.ok) {
      setDevices(r.result || []);
      pushLog(t.foundDevices((r.result || []).length));
    } else {
      pushLog(t.deviceListError(r.error || "unknown"));
    }
  }

  async function connect() {
    if (!selectedAddr) return;
    setStatus("connecting");
    setStatusText("");
    pushLog(t.connectingTo(selectedAddr));
    const r = await request({ cmd: "connect", addr: selectedAddr });
    if (r.ok) {
      setStatus("connected");
      pushLog(t.connectedLog);
      readState();
    } else {
      setStatus("error");
      setStatusText(r.error || "unknown");
      pushLog(t.connectionError(r.error || "unknown"));
    }
  }

  async function disconnect() {
    setStatus("idle");
    setStatusText("");
    pushLog(t.closing);
    await request({ cmd: "close" });
  }

  async function readState() {
    pushLog(t.readingState);
    const r = await request({ cmd: "get" });
    if (r.ok) {
      const st = r.result?.state;
      pushLog(t.state(JSON.stringify(st)));
    } else {
      pushLog(t.getError(r.error || "unknown"));
    }
  }

  async function setAmbientMode(m: AmbientMode) {
    setMode(m);
    if (status !== "connected") {
      pushLog(t.notConnected);
      return;
    }
    if (m === "custom") {
      pushLog(t.command("custom"));
      const r = await request({ cmd: "custom" });
      pushLog(r.ok ? t.commandOk("custom") : t.commandError("custom", r.error || "unknown"));
    } else if (m === "adaptive") {
      pushLog(t.command("mode adaptive"));
      const r = await request({ cmd: "mode", mode: "adaptive" });
      pushLog(r.ok ? t.commandOk("adaptive") : t.commandError("adaptive", r.error || "unknown"));
    } else {
      pushLog(t.command("anc off"));
      const r = await request({ cmd: "anc", state: "off" });
      pushLog(r.ok ? t.commandOk("anc off") : t.commandError("anc off", r.error || "unknown"));
    }
  }

  async function setAntiwindLevel(v: string) {
    setAntiwind(v);
    if (status !== "connected") return;
    pushLog(t.command(`antiwind ${v}`));
    const r = await request({ cmd: "antiwind", level: parseInt(v, 10) });
    pushLog(r.ok ? t.commandOk(`antiwind ${v}`) : t.commandError("antiwind", r.error || "unknown"));
  }

  async function setTransparencyLevel(v: number) {
    setTransparency(v);
    if (status !== "connected") return;
    pushLog(t.command(`transparency ${v}`));
    const r = await request({ cmd: "transparency", level: v });
    pushLog(r.ok ? t.commandOk(`transparency ${v}`) : t.commandError("transparency", r.error || "unknown"));
  }

  const connected = status === "connected";

  return (
    <div className="mx-auto flex min-h-screen w-full max-w-md flex-col gap-3 overflow-x-hidden bg-background px-4 py-5">
      {/* Hero */}
      <div className="relative flex flex-col items-center gap-1 rounded-2xl border border-white/10 bg-gradient-to-b from-card to-card/40 px-4 py-6">
        <div className="pointer-events-none absolute inset-x-0 top-0 h-32 rounded-t-2xl bg-primary/15 blur-3xl" />
        <Select
          value={language}
          options={[
            { value: "en", label: t.english },
            { value: "ru", label: t.russian },
          ]}
          onChange={(value) => setLanguage(value as Language)}
          aria-label={t.language}
          title={t.language}
          className="absolute right-3 top-3 z-10 h-8 w-[104px] px-2 text-xs"
        />
        <div className="relative flex h-24 w-24 items-center justify-center rounded-full bg-secondary/60">
          <img
            src={headphonesImage}
            alt="MOMENTUM 4"
            className="h-20 w-20 object-contain"
          />
        </div>
        <h1 className="relative mt-2 text-lg font-bold">MOMENTUM 4</h1>
        <p className="relative flex items-center gap-1 text-xs text-muted-foreground">
          {connected ? (
            <>
              <Wifi className="h-3.5 w-3.5 text-emerald-400" />
              {t.connected}
            </>
          ) : (
            <>
              <WifiOff className="h-3.5 w-3.5 text-muted-foreground" />
              {status === "connecting"
                ? t.connecting
                : status === "error"
                  ? `${t.error}: ${statusText}`
                  : t.disconnected}
            </>
          )}
        </p>
      </div>

      {/* Device */}
      <Section title={t.device}>
        <div className="flex flex-col gap-2">
          <div className="flex min-w-0 items-center gap-2">
            <Select
              value={selectedAddr}
              options={[
                { value: "", label: t.chooseHeadphones },
                ...devices.map((d) => ({
                  value: d.address,
                  label: `${d.name} (${d.address})`,
                })),
              ]}
              onChange={setSelectedAddr}
              className="min-w-0 flex-1"
            />
            <Button
              size="icon"
              variant="outline"
              className="shrink-0"
              onClick={refreshDevices}
              title={t.refreshDevices}
              aria-label={t.refreshDevices}
            >
              <RefreshCw className="h-4 w-4" />
            </Button>
          </div>
          <div className="flex min-w-0 gap-2">
            <Button
              variant="outline"
              className="min-w-0 flex-1"
              disabled={!selectedAddr || status === "connecting"}
              onClick={connect}
            >
              <Power className="h-4 w-4" />
              {t.connect}
            </Button>
            <Button
              variant="outline"
              className="min-w-0 flex-1"
              disabled={!connected}
              onClick={disconnect}
            >
              <PowerOff className="h-4 w-4" />
              {t.disconnect}
            </Button>
          </div>
        </div>
      </Section>

      {/* Ambient Sound Control */}
      <Section title={t.ambient}>
        <div className="flex items-start justify-around">
          <RoundButton
            icon={<AudioWaveform className="h-6 w-6" />}
            label={t.adaptive}
            active={mode === "adaptive"}
            disabled={!connected}
            onClick={() => setAmbientMode("adaptive")}
          />
          <RoundButton
            icon={<Volume2 className="h-6 w-6" />}
            label={t.custom}
            active={mode === "custom"}
            disabled={!connected}
            onClick={() => setAmbientMode("custom")}
          />
          <RoundButton
            icon={<VolumeX className="h-6 w-6" />}
            label={t.off}
            active={mode === "off"}
            disabled={!connected}
            onClick={() => setAmbientMode("off")}
          />
        </div>
      </Section>

      {/* Anti-Wind */}
      <Section title={t.antiWind}>
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
              {t[l.key]}
            </Button>
          ))}
        </div>
      </Section>

      {/* Transparency */}
      <Section title={t.transparency}>
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

      {/* Log */}
      <Section title={t.log}>
        <div className="max-h-44 overflow-y-auto rounded-lg bg-black/40 p-2 font-mono text-[11px] leading-relaxed text-muted-foreground">
          {log.length === 0 ? (
            <span className="text-muted-foreground/50">{t.empty}</span>
          ) : (
            log.map((line, i) => <div key={i}>{line}</div>)
          )}
        </div>
      </Section>

      {!bridgeReady && (
        <p className="text-center text-xs text-red-400">
          {t.bridgeUnavailable}
        </p>
      )}
    </div>
  );
}
