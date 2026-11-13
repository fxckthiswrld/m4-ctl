import * as React from "react";
import { VolumeX, Volume2, AudioWaveform, Wind, Zap, Wifi, WifiOff, Download } from "lucide-react";
import headphonesImage from "../../headphones_nobg.png";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { Select } from "@/components/ui/select";
import { Toggle } from "@/components/ui/toggle";
import { Profiles } from "@/components/Profiles";
import { cn } from "@/lib/utils";
import { translations, type Language } from "@/lib/translations";
import type { AmbientMode, DesktopAction } from "@/lib/bridge";
import { usePreferences } from "@/hooks/usePreferences";
import { useHeadphones } from "@/hooks/useHeadphones";

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
      aria-pressed={!!active}
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
  const prefs = usePreferences();
  const { language, setLanguage } = prefs;
  const t = translations[language];
  const headphones = useHeadphones(t);
  const { bridgeReady, status, statusText, mode, antiwind,
    transparency, setTransparency, transparentHearing, setTransparentHearing: setTransparentHearingEnabled,
    deviceState, busy, recovering, log, connected, customControlsDisabled,
    setAmbientMode, setAntiwindLevel,
    commitTransparencyLevel, onInteractionChange } = headphones;
  const [tab, setTab] = React.useState<"controls" | "settings" | "diagnostics">("controls");
  const [exporting, setExporting] = React.useState(false);
  const desktopHandler = React.useRef<(event: DesktopAction) => void>(() => {});
  desktopHandler.current = (event) => {
    let action: Promise<void> | undefined;
    if (event.action === "mode") action = setAmbientMode(event.mode);
    if (event.action === "profile") {
      const profile = prefs.profiles.find((p) => p.id === event.id);
      if (profile) action = headphones.applyProfile(profile);
    }
    if (event.action === "suspend") action = headphones.suspend();
    if (event.action === "resume") headphones.resume();
    void action?.catch((error: unknown) => headphones.pushLog(String(error)));
  };
  React.useEffect(() => window.desktop?.onAction((event) => desktopHandler.current(event)), []);
  React.useEffect(() => {
    void window.desktop?.sync({ language, connected, busy: busy || recovering,
      closeToTray: prefs.closeToTray, mode: deviceState?.anc?.enabled === false ? "off" : deviceState?.mode?.key || null,
      profiles: prefs.profiles.map(({ id, name }) => ({ id, name })) }).catch((error: unknown) => headphones.pushLog(String(error)));
  }, [language, connected, busy, recovering, prefs.closeToTray, deviceState, prefs.profiles]);

  async function exportDiagnostics() {
    if (!window.desktop) return;
    setExporting(true);
    try {
      const result = await window.desktop.exportDiagnostics({ logs: log, state: deviceState, info: headphones.info });
      if (result.saved) headphones.pushLog(t.exportSaved);
    } catch (error) { headphones.pushLog(String(error)); }
    finally { setExporting(false); }
  }

  return (
    <div className="mx-auto flex min-h-screen w-full max-w-md flex-col gap-5 overflow-x-hidden bg-background px-4 py-5">
      {/* Hero */}
      <div className="relative flex flex-col items-center gap-1 rounded-2xl border border-white/10 bg-gradient-to-b from-card to-card/40 p-4">
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
        <p className="relative text-[11px] text-muted-foreground">
          {t.battery}: {headphones.info?.battery?.length ? headphones.info.battery.map((level) => `${level}%`).join(" / ") : t.unknown}
          {" · "}{t.firmware}: {headphones.info?.firmware || t.unknown}
        </p>
        <p className="relative flex max-w-full items-center gap-1 text-xs text-muted-foreground">
          {connected ? (
            <>
              <Wifi className="h-3.5 w-3.5 text-emerald-400" />
              {recovering ? t.reconnecting : t.connected}
            </>
          ) : (
            <>
              <WifiOff className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              <span className="min-w-0 break-words">{status === "connecting"
                ? t.connecting
                : status === "error"
                  ? `${t.error}: ${statusText}`
                  : t.disconnected}</span>
            </>
          )}
        </p>
      </div>

      <nav role="tablist" aria-label="MOMENTUM 4" className="grid grid-cols-3 border-b border-white/10">
        {(["controls", "settings", "diagnostics"] as const).map((view) => (
          <button key={view} role="tab" aria-selected={tab === view} aria-controls={`panel-${view}`} id={`tab-${view}`}
            tabIndex={tab === view ? 0 : -1}
            onKeyDown={(event) => {
              const views = ["controls", "settings", "diagnostics"] as const;
              const index = views.indexOf(view);
              const next = event.key === "ArrowRight" ? views[(index + 1) % 3] : event.key === "ArrowLeft" ? views[(index + 2) % 3] : event.key === "Home" ? views[0] : event.key === "End" ? views[2] : null;
              if (next) { event.preventDefault(); setTab(next); document.getElementById(`tab-${next}`)?.focus(); }
            }}
            className={cn("min-h-10 px-1 text-xs font-medium", tab === view ? "border-b-2 border-primary text-foreground" : "text-muted-foreground")}
            onClick={() => setTab(view)}>{t[view]}</button>
        ))}
      </nav>
      <div id="panel-controls" role="tabpanel" aria-labelledby="tab-controls" hidden={tab !== "controls"}>
      <div className="flex flex-col gap-5">
      {/* Ambient Sound Control */}
      <Section title={t.ambient}>
        <div className="flex items-start justify-around">
          <RoundButton
            icon={<AudioWaveform className="h-6 w-6" />}
            label={t.adaptive}
            active={mode === "adaptive"}
            disabled={!connected || busy || recovering}
            onClick={() => setAmbientMode("adaptive")}
          />
          <RoundButton
            icon={<Volume2 className="h-6 w-6" />}
            label={t.custom}
            active={mode === "custom"}
            disabled={!connected || busy || recovering}
            onClick={() => setAmbientMode("custom")}
          />
          <RoundButton
            icon={<VolumeX className="h-6 w-6" />}
            label={t.off}
            active={mode === "off"}
            disabled={!connected || busy || recovering}
            onClick={() => setAmbientMode("off")}
          />
        </div>
        <div className="mt-3 flex items-center justify-between border-t border-white/10 pt-3">
          <span className="text-xs font-medium text-muted-foreground">{t.transparentHearing}</span>
          <Toggle
            aria-label={t.transparentHearing}
            checked={transparentHearing ?? false}
            disabled={!connected || busy || recovering}
            onChange={setTransparentHearingEnabled}
          />
        </div>
      </Section>

      {/* Custom controls */}
      <Section title={t.custom}>
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <h3 className="text-xs font-medium text-muted-foreground">{t.antiWind}</h3>
            <div className="grid grid-cols-3 gap-2">
              {ANTIWIND_LEVELS.map((l) => (
                <Button
                  key={l.value}
                  variant={antiwind === l.value ? "default" : "secondary"}
                  aria-pressed={antiwind === l.value}
                  disabled={customControlsDisabled}
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
          </div>

          <div className="flex flex-col gap-1 border-t border-white/10 pt-4">
            <div className="flex justify-between text-xs font-medium">
              {/* Each track half covers its full 0-100% range: left of the
                  zero point is ANC, right is transparency. */}
              <span className={transparency < 50 ? "text-primary" : "text-muted-foreground"}>ANC {Math.max(0, (50 - transparency) * 2)}%</span>
              <span className={transparency > 50 ? "text-primary" : "text-muted-foreground"}>{Math.max(0, (transparency - 50) * 2)}% {t.transparency}</span>
            </div>
            <Slider
              aria-label={t.ancSlider}
              value={transparency}
              min={0}
              max={100}
              step={5}
              zeroPoint={50}
              disabled={customControlsDisabled}
              onChange={setTransparency}
              onCommit={commitTransparencyLevel}
              onInteractionChange={onInteractionChange}
            />
          </div>
        </div>
      </Section>

      <Section title={t.profiles}>
        <Profiles profiles={prefs.profiles} setProfiles={prefs.setProfiles} state={deviceState}
          disabled={!connected || busy || recovering} apply={headphones.applyProfile} t={t} />
      </Section>
      </div>
      </div>

      <div id="panel-settings" role="tabpanel" aria-labelledby="tab-settings" hidden={tab !== "settings"}>
        <Section title={t.settings}>
          <div className="flex flex-col gap-5">
            <div className="flex items-center justify-between gap-4">
              <span className="text-sm">{t.closeToTray}</span>
              <Toggle aria-label={t.closeToTray} checked={prefs.closeToTray} onChange={prefs.setCloseToTray} />
            </div>
            <p className="text-xs text-muted-foreground">{t.autoConnectHint}</p>
          </div>
        </Section>
      </div>

      <div id="panel-diagnostics" role="tabpanel" aria-labelledby="tab-diagnostics" hidden={tab !== "diagnostics"}>
        <Section title={t.log}>
          <Button variant="outline" className="mb-3 w-full" disabled={exporting || !window.desktop} onClick={exportDiagnostics}>
            <Download className="h-4 w-4" />{t.exportLog}
          </Button>
          <div className="h-64 overflow-y-auto rounded-lg bg-black/40 p-2 font-mono text-[11px] leading-relaxed text-muted-foreground break-words">
            {log.length === 0 ? <span>{t.empty}</span> : log.map((line, index) => <div key={index}>{line}</div>)}
          </div>
        </Section>
      </div>

      {!bridgeReady && (
        <p className="text-center text-xs text-red-400">
          {t.bridgeUnavailable}
        </p>
      )}
    </div>
  );
}
