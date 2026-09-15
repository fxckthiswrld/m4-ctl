const fs = require("node:fs");
const path = require("node:path");

function createDesktop({ app, Menu, Tray, nativeImage, powerMonitor, dialog, ipcMain }, showWindow, publish, stopBridge) {
  let state = { language: "en", connected: false, busy: false, canConnect: false, closeToTray: false, mode: null, profiles: [] };
  let tray;
  const labels = {
    en: { open: "Open Momentum 4 Control", connect: "Connect", disconnect: "Disconnect", cancel: "Cancel", adaptive: "Adaptive", custom: "Custom", off: "ANC off", profiles: "Profiles", quit: "Quit", disconnected: "Not connected", connected: "Connected" },
    ru: { open: "Открыть Momentum 4 Control", connect: "Подключить", disconnect: "Отключить", cancel: "Отменить", adaptive: "Адаптивный", custom: "Настраиваемый", off: "ANC выкл.", profiles: "Профили", quit: "Выход", disconnected: "Не подключено", connected: "Подключено" },
  };
  const action = (value) => publish("desktop:action", value);
  function updateMenu() {
    if (!tray) return;
    const t = labels[state.language];
    const available = state.connected && !state.busy;
    tray.setToolTip(`Momentum 4 Control: ${state.connected ? t.connected : t.disconnected}`);
    tray.setContextMenu(Menu.buildFromTemplate([
      { label: t.open, click: showWindow },
      { type: "separator" },
      { label: t.connect, enabled: state.canConnect && !state.connected && !state.busy, click: () => action({ action: "connect" }) },
      { label: state.busy ? t.cancel : t.disconnect, enabled: state.connected || state.busy, click: () => action({ action: "disconnect" }) },
      { type: "separator" },
      ...["adaptive", "custom", "off"].map((mode) => ({ label: t[mode], type: "checkbox", checked: state.mode === mode, enabled: available, click: () => action({ action: "mode", mode }) })),
      { label: t.profiles, enabled: available && state.profiles.length > 0,
        submenu: state.profiles.map((p) => ({ label: p.name.replace(/&/g, "&&"), click: () => action({ action: "profile", id: p.id }) })) },
      { type: "separator" },
      { label: t.quit, click: () => app.quit() },
    ]));
  }
  try {
    const icon = nativeImage.createFromPath(path.join(__dirname, "icon.png")).resize({ width: 20, height: 20 });
    tray = new Tray(icon);
    tray.on("click", showWindow);
    updateMenu();
  } catch (error) { publish("bridge:log", `Tray unavailable: ${error.message}`); }

  ipcMain.handle("desktop:sync", (_event, value) => {
    if (!value || !["en", "ru"].includes(value.language) ||
        ["connected", "busy", "canConnect", "closeToTray"].some((key) => typeof value[key] !== "boolean") ||
        ![null, "adaptive", "custom", "off", "comfort"].includes(value.mode) ||
        !Array.isArray(value.profiles) || value.profiles.length > 20 || value.profiles.some((p) =>
          !p || typeof p.id !== "string" || p.id.length > 80 || typeof p.name !== "string" || p.name.length > 40)) throw new Error("invalid desktop state");
    state = value;
    updateMenu();
  });

  ipcMain.handle("desktop:export", async (_event, snapshot) => {
    if (!snapshot || !Array.isArray(snapshot.logs) || snapshot.logs.length > 200 || snapshot.logs.some((line) => typeof line !== "string" || line.length > 4200)) throw new Error("invalid diagnostics");
    const report = JSON.stringify({ version: app.getVersion(), platform: process.platform, architecture: process.arch,
      exportedAt: new Date().toISOString(), logs: snapshot.logs, state: snapshot.state ?? null, info: snapshot.info ?? null }, null, 2);
    if (report.length > 2_000_000) throw new Error("diagnostics too large");
    const { canceled, filePath } = await dialog.showSaveDialog({ title: "Momentum 4 Control",
      defaultPath: path.join(app.getPath("downloads"), "momentum4-diagnostics.json"),
      filters: [{ name: "JSON", extensions: ["json"] }] });
    if (canceled || !filePath) return { saved: false };
    await fs.promises.writeFile(filePath, report.replace(/\b(?:[0-9A-F]{2}[:-]){5}[0-9A-F]{2}\b/gi, "[Bluetooth address]"), "utf8");
    return { saved: true };
  });
  const suspend = () => { action({ action: "suspend" }); stopBridge("system suspended", true); };
  const resume = () => action({ action: "resume" });
  powerMonitor.on("suspend", suspend);
  powerMonitor.on("resume", resume);
  return {
    shouldHide: () => !!tray && state.closeToTray,
    disconnected: () => { state = { ...state, connected: false, busy: false, mode: null }; updateMenu(); },
    dispose: () => { powerMonitor.removeListener("suspend", suspend); powerMonitor.removeListener("resume", resume); tray?.destroy(); tray = null; },
  };
}
module.exports = { createDesktop };
