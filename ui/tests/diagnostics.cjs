const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { EventEmitter } = require("node:events");
const { createDesktop } = require("../electron/desktop.cjs");

test("diagnostic export saves metadata and redacts Bluetooth addresses", async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "m4-diagnostics-"));
  const target = path.join(directory, "report.json");
  const handlers = new Map();
  let cancelled = false;
  const desktop = createDesktop({
    app: { getVersion: () => "0.2.5", getPath: () => directory },
    ipcMain: { handle: (name, handler) => handlers.set(name, handler) },
    powerMonitor: new EventEmitter(),
    dialog: { showSaveDialog: async () => ({ canceled: cancelled, filePath: target }) },
    nativeImage: { createFromPath: () => { throw new Error("no tray"); } },
    getBridgeDiagnostics: () => ({
      events: [{ at: "2026-01-01T00:00:00Z", event: "connection-lost" }, { at: "2026-01-01T00:00:05Z", event: "reconnecting", background: true }],
      stderr: ["TX: ff 03 00 04 04 95 1a 04 01", "connected to AA:BB:CC:DD:EE:FF"],
    }),
  }, () => {}, () => {}, () => {});
  try {
    const exportReport = handlers.get("desktop:export");
    const snapshot = { version: "incorrect", logs: ["connect AA:BB:CC:DD:EE:FF", "device aa-bb-cc-dd-ee-ff"], state: null, info: { battery: [82], firmware: "3.18.0" } };
    assert.deepEqual(await exportReport({}, snapshot), { saved: true });
    const source = fs.readFileSync(target, "utf8");
    const report = JSON.parse(source);
    assert.equal(report.version, "0.2.5");
    assert.equal(report.info.firmware, "3.18.0");
    assert.equal(report.bridge.events.length, 2);
    assert.equal(report.bridge.events[1].event, "reconnecting");
    assert.equal(report.bridge.stderr.length, 2);
    assert.ok(!/AA:BB|aa-bb/.test(source));
    assert.match(source, /Bluetooth address/);
    cancelled = true;
    assert.deepEqual(await exportReport({}, snapshot), { saved: false });
    await assert.rejects(exportReport({}, { logs: [123] }), /invalid diagnostics/);
  } finally {
    desktop.dispose();
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

test("diagnostic export works without bridge diagnostics", async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "m4-diagnostics-"));
  const target = path.join(directory, "report.json");
  const handlers = new Map();
  const desktop = createDesktop({
    app: { getVersion: () => "0.2.5", getPath: () => directory },
    ipcMain: { handle: (name, handler) => handlers.set(name, handler) },
    powerMonitor: new EventEmitter(),
    dialog: { showSaveDialog: async () => ({ canceled: false, filePath: target }) },
    nativeImage: { createFromPath: () => { throw new Error("no tray"); } },
  }, () => {}, () => {}, () => {});
  try {
    const exportReport = handlers.get("desktop:export");
    await exportReport({}, { logs: [] });
    const report = JSON.parse(fs.readFileSync(target, "utf8"));
    assert.equal(report.bridge, null);
  } finally {
    desktop.dispose();
    fs.rmSync(directory, { recursive: true, force: true });
  }
});
