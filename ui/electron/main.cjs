const { app, BrowserWindow, ipcMain, Menu, Tray, nativeImage, powerMonitor, dialog } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");
const { createDesktop } = require(path.join(__dirname, "desktop.cjs"));

let mainWindow = null;
let bridge = null;
let bridgeOut = "";
let desktop;
let quitting = false;
let rendererFailures = [];
let bridgeStatus = { event: "stopped" };
const pending = new Map();
const COMMAND_TIMEOUT_MS = 60000;

function publish(channel, message) {
  if (mainWindow && !mainWindow.isDestroyed() && !mainWindow.webContents.isDestroyed() && !mainWindow.webContents.isCrashed()) {
    mainWindow.webContents.send(channel, message);
  }
}

function publishEvent(event) {
  if (event.event === "stopped") desktop?.disconnected();
  if (["ready", "starting", "stopped"].includes(event.event)) bridgeStatus = event;
  publish("bridge:event", event);
}

function failPending(error, cancelled = false) {
  for (const [id, timer] of pending) {
    clearTimeout(timer);
    publish("bridge:reply", { id, ok: false, error, cancelled });
  }
  pending.clear();
}

function startBridge() {
  if (bridge) return;
  publishEvent({ event: "starting" });

  if (app.isPackaged) {
    const bridgeName = process.platform === "win32" ? "m4-bridge.exe" : "m4-bridge";
    const nativeExecutable = path.join(process.resourcesPath, "bridge", process.arch, bridgeName);
    const executable = process.platform === "darwin" && fs.existsSync(nativeExecutable) ? nativeExecutable : path.join(
      process.resourcesPath,
      "bridge",
      bridgeName
    );
    const options = {
      cwd: process.resourcesPath,
      shell: false,
    };
    if (process.platform === "win32") options.windowsHide = true;
    bridge = spawn(executable, [], options);
  } else {
    // Run Python directly so cancellation terminates the bridge, not a uv parent.
    const root = path.join(__dirname, "..", "..");
    const python = path.join(root, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
    bridge = spawn(python, ["-u", "bridge.py"], {
      cwd: root,
      shell: false,
      windowsHide: true,
    });
  }
  bridgeOut = "";
  const child = bridge;
  child.stdout.setEncoding("utf8");
  child.stderr.setEncoding("utf8");

  bridge.stdout.on("data", (d) => {
    if (bridge !== child) return;
    bridgeOut += d.toString();
    let idx;
    while ((idx = bridgeOut.indexOf("\n")) >= 0) {
      const line = bridgeOut.slice(0, idx).trim();
      bridgeOut = bridgeOut.slice(idx + 1);
      if (!line) continue;
      try {
        const msg = JSON.parse(line);
        if (msg.event) {
          publishEvent(msg);
          continue;
        }
        const id = String(msg.id);
        if (!pending.has(id)) continue;
        clearTimeout(pending.get(id));
        pending.delete(id);
        publishEvent({ event: "operation-complete" });
        publish("bridge:reply", msg);
      } catch {
        // не-JSON вывод (warnings и т.п.) — игнорируем
      }
    }
  });

  bridge.stderr.on("data", (d) => {
    if (bridge === child) publish("bridge:log", d.toString());
  });
  bridge.stdin.on("error", (error) => {
    if (bridge === child) publish("bridge:log", `[bridge] ошибка stdin: ${error.message}`);
  });

  bridge.on("error", (error) => {
    if (bridge !== child) return;
    stopBridge(error.message);
    publish("bridge:log", `[bridge] ошибка запуска: ${error.message}`);
  });
  bridge.on("exit", (code) => {
    if (bridge !== child) return;
    bridge = null;
    failPending(`bridge exited (${code})`);
    publishEvent({ event: "stopped", error: `bridge exited (${code})` });
    publish("bridge:log", `[bridge] процесс завершён (${code})`);
  });
}

function stopBridge(error = "bridge stopped", cancelled = false) {
  if (bridge) {
    const child = bridge;
    bridge = null;
    child.stdin.end();
    if (app.isPackaged && process.platform === "win32") {
      // PyInstaller onefile uses a child process; terminate the entire owned tree.
      const killer = spawn("taskkill", ["/pid", String(child.pid), "/T", "/F"], { windowsHide: true });
      killer.on("error", () => child.kill());
      killer.on("exit", (code) => { if (code) child.kill(); });
    } else child.kill();
  }
  failPending(error, cancelled);
  publishEvent({ event: "stopped", error, cancelled });
}

function sendToBridge(msg) {
  if (!msg || typeof msg.id !== "string" || typeof msg.cmd !== "string") throw new Error("invalid bridge request");
  if (pending.has(msg.id)) throw new Error("duplicate request id");
  if (!bridge) startBridge();
  if (!bridge || !bridge.stdin || bridge.stdin.destroyed || bridge.stdin.writableEnded) {
    throw new Error("bridge process is unavailable");
  }
  const timer = setTimeout(() => stopBridge("command timed out"), COMMAND_TIMEOUT_MS + 1000);
  pending.set(msg.id, timer);
  bridge.stdin.write(JSON.stringify({ ...msg, deadline_ms: Date.now() + COMMAND_TIMEOUT_MS }) + "\n", (error) => {
    if (error && pending.has(msg.id)) stopBridge(error.message);
  });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 420,
    height: 860,
    resizable: false,
    maximizable: false,
    autoHideMenuBar: true,
    title: "Momentum 4 Control",
    icon: path.join(__dirname, "icon.ico"),
    backgroundColor: "#0d0d10",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      backgroundThrottling: false,
    },
  });
  mainWindow.setMenu(null);
  mainWindow.on("close", (event) => {
    if (!quitting && desktop?.shouldHide()) { event.preventDefault(); mainWindow.hide(); }
  });

  const distIndex = path.join(__dirname, "..", "dist", "index.html");
  if (process.env.VITE_DEV_SERVER_URL) {
    mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL);
  } else if (fs.existsSync(distIndex)) {
    mainWindow.loadFile(distIndex);
  } else {
    // dev без dist: ждём Vite dev server
    mainWindow.loadURL("http://localhost:5173");
  }

  mainWindow.webContents.on("render-process-gone", () => {
    if (quitting) return;
    stopBridge("interface process stopped");
    const now = Date.now();
    rendererFailures = rendererFailures.filter((time) => now - time < 60000);
    rendererFailures.push(now);
    if (rendererFailures.length > 2) {
      const russian = app.getLocale().startsWith("ru");
      dialog.showErrorBox("Momentum 4 Control", russian
        ? "Не удалось восстановить окно приложения. Приложение будет закрыто."
        : "The application window could not recover. The application will close.");
      app.quit();
      return;
    }
    mainWindow.webContents.reload();
  });
}

function showWindow() {
  if (!mainWindow || mainWindow.isDestroyed()) createWindow();
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show(); mainWindow.focus();
}

function initialize() {
  app.on("second-instance", () => { if (app.isReady()) showWindow(); });
  app.whenReady().then(() => {
    Menu.setApplicationMenu(null);
    createWindow();
    desktop = createDesktop({ app, Menu, Tray, nativeImage, powerMonitor, dialog, ipcMain }, showWindow, publish, stopBridge);
    startBridge();

    app.on("activate", showWindow);
  });

  app.on("window-all-closed", () => {
    stopBridge();
    if (process.platform !== "darwin") app.quit();
  });

  app.on("before-quit", () => {
    quitting = true;
    desktop?.dispose();
    stopBridge();
  });

  ipcMain.handle("bridge:cmd", (_e, msg) => {
    sendToBridge(msg);
    return { queued: true };
  });

  ipcMain.handle("bridge:status", () => bridgeStatus);
  ipcMain.handle("bridge:cancel", () => stopBridge("cancelled", true));
}

if (app.requestSingleInstanceLock()) initialize();
else app.quit();
