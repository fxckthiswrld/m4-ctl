import * as React from "react";
import type { Profile } from "@/lib/bridge";
import { translations, type Language } from "@/lib/translations";

export function usePreferences() {
  const [language, setLanguage] = React.useState<Language>(() => {
    const value = localStorage.getItem("m4-language");
    return value === "en" || value === "ru" ? value : navigator.language.startsWith("ru") ? "ru" : "en";
  });
  const [autoConnect, setAutoConnect] = React.useState(() => localStorage.getItem("m4-auto-connect") === "true");
  const [closeToTray, setCloseToTray] = React.useState(() => localStorage.getItem("m4-close-to-tray") === "true");
  const [profiles, setProfiles] = React.useState<Profile[]>(() => {
    try {
      const saved: unknown = JSON.parse(localStorage.getItem("m4-profiles") || "null");
      if (Array.isArray(saved) && saved.length <= 20 && saved.every(isProfile) && new Set(saved.map((p) => p.id)).size === saved.length) return saved;
    } catch { /* Recover from an invalid stored profile list. */ }
    const t = translations[language];
    return [
      { id: "work", name: t.work, mode: "custom", antiwind: 0, transparency: 0 },
      { id: "street", name: t.street, mode: "custom", antiwind: 2, transparency: 40 },
      { id: "transport", name: t.transport, mode: "adaptive", antiwind: 0, transparency: 0 },
    ];
  });
  React.useEffect(() => {
    localStorage.setItem("m4-language", language);
    document.documentElement.lang = language;
  }, [language]);
  React.useEffect(() => { localStorage.setItem("m4-auto-connect", String(autoConnect)); }, [autoConnect]);
  React.useEffect(() => { localStorage.setItem("m4-close-to-tray", String(closeToTray)); }, [closeToTray]);
  React.useEffect(() => { localStorage.setItem("m4-profiles", JSON.stringify(profiles)); }, [profiles]);
  return { language, setLanguage, autoConnect, setAutoConnect, closeToTray, setCloseToTray, profiles, setProfiles };
}

function isProfile(value: unknown): value is Profile {
  if (!value || typeof value !== "object") return false;
  const p = value as Partial<Profile>;
  return typeof p.id === "string" && p.id.length > 0 && p.id.length <= 80 &&
    typeof p.name === "string" && p.name.trim().length > 0 && p.name.length <= 40 &&
    ["adaptive", "custom", "off"].includes(p.mode || "") &&
    typeof p.antiwind === "number" && [0, 1, 2].includes(p.antiwind) &&
    typeof p.transparency === "number" && Number.isInteger(p.transparency) && p.transparency >= 0 && p.transparency <= 100;
}
