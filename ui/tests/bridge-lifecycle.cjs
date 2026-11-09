const assert = require("node:assert/strict");
const { test } = require("node:test");
const { EventEmitter } = require("node:events");
const { PassThrough } = require("node:stream");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

async function harness({ packaged = false, platform = "win32", arch = "x64", nativeBridge = false, onedirBridge = false, ownsLock = true } = {}) {
  const handlers = new Map();
  const timers = new Map();
  const messages = [];
  const children = [];
  const windows = [];
  const errors = [];
  let tray;
  const app = new EventEmitter();
  app.isPackaged = packaged;
  app.requestSingleInstanceLock = () => ownsLock;
  app.isReady = () => true;
  app.getLocale = () => "en";
  app.whenReady = () => Promise.resolve();
  app.quit = () => { app.quitCalled = true; app.emit("before-quit"); };
  const electron = {
    app,
    dialog: { showErrorBox: (title, message) => errors.push({ title, message }) },
    ipcMain: { handle: (name, callback) => handlers.set(name, callback) },
    powerMonitor: new EventEmitter(),
    nativeImage: { createFromPath: () => ({ resize: () => ({}) }) },
    Tray: class extends EventEmitter {
      constructor() { super(); tray = this; }
      setToolTip() {}
      setContextMenu(menu) { this.menu = menu; }
      destroy() {}
    },
    Menu: { setApplicationMenu() {}, buildFromTemplate: (menu) => menu },
    BrowserWindow: class extends EventEmitter {
      constructor() {
        super(); windows.push(this);
        this.webContents = new EventEmitter();
        this.webContents.isDestroyed = () => false;
        this.webContents.isCrashed = () => false;
        this.webContents.send = (channel, message) => messages.push({ channel, message });
        this.webContents.reload = () => { this.reloads = (this.reloads || 0) + 1; };
      }
      isDestroyed() { return false; }
      setMenu() {}
      loadURL() {}
      loadFile() {}
      hide() { this.hidden = true; }
      show() { this.hidden = false; }
      focus() { this.focused = true; }
      isMinimized() { return !!this.minimized; }
      restore() { this.minimized = false; }
    },
  };
  let timerId = 0;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../electron/main.cjs"), "utf8"), {
    __dirname: path.join(__dirname, "../electron"),
    process: { platform, arch, resourcesPath: "/app/resources", env: {} },
    setTimeout: (callback, delay) => { timers.set(++timerId, { callback, delay }); return timerId; },
    clearTimeout: (id) => timers.delete(id),
    require: (name) => {
      if (name === "electron") return electron;
      if (name === "fs") return {
        ...fs,
        existsSync: (filename) => {
          if (nativeBridge && filename === path.join("/app/resources", "bridge", arch, "m4-bridge")) return true;
          if (onedirBridge && platform === "win32" && filename === path.join("/app/resources", "bridge", "win", "m4-bridge", "m4-bridge.exe")) return true;
          if (onedirBridge && platform === "darwin" && filename === path.join("/app/resources", "bridge", "mac", "m4-bridge", "m4-bridge")) return true;
          return false;
        },
      };
      if (name !== "child_process") return require(name);
      return { spawn: (executable) => {
        const child = new EventEmitter();
        child.executable = executable;
        child.stdin = new PassThrough();
        child.stdout = new PassThrough();
        child.stderr = new PassThrough();
        child.kill = () => { child.killed = true; };
        children.push(child);
        return child;
      } };
    },
  });
  await Promise.resolve();
  const invoke = (name, message) => handlers.get(`bridge:${name}`)({}, message);
  const output = (child, message) => child.stdout.write(JSON.stringify(message) + "\n");
  const replies = () => messages.filter((entry) => entry.channel === "bridge:reply").map((entry) => entry.message);
  return { children, messages, timers, invoke, output, replies, windows, tray, app, errors, powerMonitor: electron.powerMonitor,
    desktop: (value) => handlers.get("desktop:sync")({}, value) };
}

test("second launch exits without creating another window or Bluetooth bridge", async () => {
  const h = await harness({ ownsLock: false });
  assert.equal(h.app.quitCalled, true);
  assert.equal(h.windows.length, 0);
  assert.equal(h.children.length, 0);
});

test("second instance restores and focuses the existing window", async () => {
  const h = await harness();
  h.windows[0].hide();
  h.windows[0].minimized = true;
  h.app.emit("second-instance");
  assert.equal(h.windows.length, 1);
  assert.equal(h.children.length, 1);
  assert.equal(h.windows[0].hidden, false);
  assert.equal(h.windows[0].minimized, false);
  assert.equal(h.windows[0].focused, true);
});

test("renderer crash stops pending commands and reloads with a bounded retry count", async () => {
  const h = await harness();
  h.invoke("cmd", { id: "pending", cmd: "get" });
  h.windows[0].webContents.emit("render-process-gone");
  assert.equal(h.children[0].killed, true);
  assert.equal(h.replies()[0].ok, false);
  assert.equal(h.timers.size, 0);
  assert.equal(h.windows[0].reloads, 1);
  h.invoke("cmd", { id: "after-reload", cmd: "list" });
  assert.equal(h.children.length, 2);
  h.windows[0].webContents.emit("render-process-gone");
  assert.equal(h.children[1].killed, true);
  assert.equal(h.windows[0].reloads, 2);
  h.windows[0].webContents.emit("render-process-gone");
  assert.equal(h.windows[0].reloads, 2);
  assert.equal(h.errors.length, 1);
  assert.equal(h.app.quitCalled, true);
});

test("renderer exit during application shutdown does not restart it", async () => {
  const h = await harness();
  h.app.quit();
  h.windows[0].webContents.emit("render-process-gone");
  assert.equal(h.windows[0].reloads, undefined);
  assert.equal(h.errors.length, 0);
});

test("packaged apps select the matching bridge layout with fallbacks", async () => {
  // Native per-arch layout is kept for legacy universal-style bundles; the
  // standard builds ship the onedir folder, with the onefile as last resort
  // for older installs.
  for (const arch of ["arm64", "x64"]) {
    const native = await harness({ packaged: true, platform: "darwin", arch, nativeBridge: true });
    assert.equal(native.children[0].executable, path.join("/app/resources", "bridge", arch, "m4-bridge"));
  }
  const windowsOnedir = await harness({ packaged: true, onedirBridge: true });
  assert.equal(windowsOnedir.children[0].executable, path.join("/app/resources", "bridge", "win", "m4-bridge", "m4-bridge.exe"));
  const macOnedir = await harness({ packaged: true, platform: "darwin", arch: "arm64", onedirBridge: true });
  assert.equal(macOnedir.children[0].executable, path.join("/app/resources", "bridge", "mac", "m4-bridge", "m4-bridge"));
  const windows = await harness({ packaged: true });
  assert.equal(windows.children[0].executable, path.join("/app/resources", "bridge", "m4-bridge.exe"));
  const macLegacy = await harness({ packaged: true, platform: "darwin", arch: "arm64" });
  assert.equal(macLegacy.children[0].executable, path.join("/app/resources", "bridge", "m4-bridge"));
});

test("ready is available to renderers that subscribe after startup", async () => {
  const h = await harness();
  h.output(h.children[0], { event: "ready" });
  assert.equal(h.invoke("status").event, "ready");
});

test("cancellation asks the bridge to abort in-flight commands", async () => {
  const h = await harness();
  h.invoke("cmd", { id: "a", cmd: "get" });
  h.invoke("cmd", { id: "b", cmd: "anc", state: "on" });
  h.invoke("cancel");
  // The cancel request is forwarded to the running bridge; no restart happens.
  const sent = h.children[0].stdin.read().toString().trim().split("\n").map((line) => JSON.parse(line));
  assert.deepEqual(sent.map((message) => message.cmd), ["get", "anc", "cancel"]);
  assert.equal(h.children.length, 1);
  assert.ok(!h.children[0].killed);
  assert.equal(h.replies().length, 0);
  assert.equal(h.timers.size, 3);
  // The bridge aborts each in-flight command and reports it as cancelled.
  h.output(h.children[0], { id: "a", ok: false, error: "cancelled", cancelled: true });
  h.output(h.children[0], { id: "b", ok: false, error: "cancelled", cancelled: true });
  h.output(h.children[0], { id: sent[2].id, ok: true, result: { cancelled: ["a", "b"] } });
  assert.equal(h.replies().length, 3);
  assert.equal(h.replies().filter((reply) => reply.cancelled).length, 2);
  assert.equal(h.timers.size, 0);
});

test("watchdog kills a stuck process and the next request starts a new one", async () => {
  const h = await harness();
  h.invoke("cmd", { id: "a", cmd: "connect", addr: "AA:BB:CC:DD:EE:FF" });
  const payload = JSON.parse(h.children[0].stdin.read().toString());
  assert.ok(payload.deadline_ms > Date.now());
  const timer = [...h.timers.values()][0];
  assert.equal(timer.delay, 61000);
  timer.callback();
  assert.equal(h.children[0].killed, true);
  assert.equal(h.replies()[0].error, "command timed out");
  h.invoke("cmd", { id: "b", cmd: "list" });
  assert.equal(h.children.length, 2);
});

test("process exit and spawn failure release pending requests immediately", async () => {
  for (const event of ["exit", "error"]) {
    const h = await harness();
    h.invoke("cmd", { id: "a", cmd: "list" });
    h.children[0].emit(event, event === "exit" ? 1 : new Error("spawn failed"));
    assert.equal(h.replies().length, 1);
    assert.equal(h.replies()[0].ok, false);
    assert.equal(h.timers.size, 0);
    assert.equal(h.invoke("status").event, "stopped");
  }
});

test("tray uses shared actions and respects busy and close preferences", async () => {
  const h = await harness();
  const state = { language: "en", connected: true, busy: false, canConnect: true, closeToTray: true, mode: "custom", profiles: [{ id: "work", name: "Work" }] };
  h.desktop(state);
  const adaptive = h.tray.menu.find((item) => item.label === "Adaptive");
  assert.equal(adaptive.enabled, true);
  adaptive.click();
  assert.equal(h.messages.at(-1).message.action, "mode");
  let prevented = false;
  h.windows[0].emit("close", { preventDefault: () => { prevented = true; } });
  assert.equal(prevented, true);
  assert.equal(h.windows[0].hidden, true);
  h.tray.emit("click");
  assert.equal(h.windows[0].hidden, false);
  h.windows[0].hide();
  h.app.emit("activate");
  assert.equal(h.windows[0].hidden, false, "dock activation must reopen a hidden window");
  h.desktop({ ...state, busy: true });
  assert.equal(h.tray.menu.find((item) => item.label === "Adaptive").enabled, false);
  assert.ok(h.tray.menu.find((item) => item.label === "Cancel"));
  h.app.quit();
  prevented = false;
  h.windows[0].emit("close", { preventDefault: () => { prevented = true; } });
  assert.equal(prevented, false, "Quit must bypass close-to-tray");
});

test("sleep stops the bridge and resume is delivered to the controller", async () => {
  const h = await harness();
  h.powerMonitor.emit("suspend");
  assert.equal(h.children[0].killed, true);
  h.powerMonitor.emit("resume");
  assert.equal(h.messages.at(-1).message.action, "resume");
});
