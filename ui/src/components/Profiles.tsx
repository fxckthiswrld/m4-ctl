import * as React from "react";
import { Play, Save, Pencil, Trash2 } from "lucide-react";
import { Button } from "./ui/button";
import { Select } from "./ui/select";
import type { DeviceState, Profile, ProfileSettings } from "@/lib/bridge";
import type { Translations } from "@/lib/translations";

export function Profiles({ profiles, setProfiles, state, disabled, apply, t }: {
  profiles: Profile[]; setProfiles: React.Dispatch<React.SetStateAction<Profile[]>>;
  state: DeviceState | null; disabled: boolean; apply: (profile: ProfileSettings) => Promise<void>; t: Translations;
}) {
  const [selected, setSelected] = React.useState(profiles[0]?.id || "");
  const [name, setName] = React.useState("");
  const profile = profiles.find((p) => p.id === selected);
  const mode = state?.anc?.enabled === false ? "off" : state?.mode?.key;
  const canSave = !disabled && mode && mode !== "comfort" &&
    (mode !== "custom" || (state?.mode?.antiwind != null && state.transparency?.level != null));
  function save() {
    if (!canSave || !mode || !name.trim() || profiles.length >= 20) return;
    const id = crypto.randomUUID();
    setProfiles((items) => [...items, { id, name: name.trim(), mode, antiwind: state?.mode?.antiwind ?? 0, transparency: state?.transparency?.level ?? 0 }]);
    setSelected(id); setName("");
  }
  function remove() {
    setProfiles((items) => items.filter((item) => item.id !== selected));
    setSelected(profiles.find((item) => item.id !== selected)?.id || "");
  }
  return <div className="flex flex-col gap-3">
    <div className="flex min-w-0 gap-2">
      <Select aria-label={t.profiles} className="min-w-0 flex-1" value={selected}
        options={profiles.length ? profiles.map((p) => ({ value: p.id, label: p.name })) : [{ value: "", label: t.empty }]}
        onChange={setSelected} />
      <Button size="icon" variant="outline" title={t.applyProfile} aria-label={t.applyProfile} disabled={disabled || !profile} onClick={() => profile && void apply(profile)}><Play className="h-4 w-4" /></Button>
      <Button size="icon" variant="outline" title={t.deleteProfile} aria-label={t.deleteProfile} disabled={!profile || disabled} onClick={remove}><Trash2 className="h-4 w-4" /></Button>
    </div>
    <div className="flex min-w-0 gap-2">
      <input aria-label={t.profileName} placeholder={t.profileName} value={name} maxLength={40} onChange={(e) => setName(e.target.value)}
        className="h-10 min-w-0 flex-1 rounded-md border bg-secondary px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" />
      <Button size="icon" variant="outline" title={t.saveProfile} aria-label={t.saveProfile} disabled={!canSave || !name.trim() || profiles.length >= 20} onClick={save}><Save className="h-4 w-4" /></Button>
      <Button size="icon" variant="outline" title={t.renameProfile} aria-label={t.renameProfile} disabled={!profile || !name.trim() || disabled}
        onClick={() => { setProfiles((items) => items.map((p) => p.id === selected ? { ...p, name: name.trim() } : p)); setName(""); }}><Pencil className="h-4 w-4" /></Button>
    </div>
  </div>;
}
