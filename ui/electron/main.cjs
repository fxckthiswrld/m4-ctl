const { app, BrowserWindow, ipcMain, Menu } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");

let mainWindow = null;
let bridge = null;
let bridgeOut = "";

function startBridge() {
  if (bridge) return;

  if (app.isPackaged) {
    const bridgeName = process.platform === "win32" ? "m4-bridge.exe" : "m4-bridge";
    const executable = path.join(
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
    // В dev-режиме мост запускается из корня репозитория через uv.
    const root = path.join(__dirname, "..", "..");
    bridge = spawn("uv", ["run", "python", "bridge.py"], {
      cwd: root,
      shell: true,
    });
  }
  bridgeOut = "";

  bridge.stdout.on("data", (d) => {
    bridgeOut += d.toString();
    let idx;
    while ((idx = bridgeOut.indexOf("\n")) >= 0) {
      const line = bridgeOut.slice(0, idx).trim();
      bridgeOut = bridgeOut.slice(idx + 1);
      if (!line) continue;
      try {
        const msg = JSON.parse(line);
        if (mainWindow && !mainWindow.isDestroyed()) {
          mainWindow.webContents.send("bridge:reply", msg);
        }
      } catch {
        // не-JSON вывод (warnings и т.п.) — игнорируем
      }
    }
  });

  bridge.stderr.on("data", (d) => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send("bridge:log", d.toString());
    }
  });

  bridge.on("exit", (code) => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send("bridge:log", `[bridge] процесс завершён (${code})`);
    }
    bridge = null;
  });
}

function stopBridge() {
  if (bridge) {
    bridge.stdin.end();
    bridge.kill();
    bridge = null;
  }
}

function sendToBridge(msg) {
  if (!bridge) startBridge();
  bridge.stdin.write(JSON.stringify(msg) + "\n");
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
    },
  });
  mainWindow.setMenu(null);

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
    // на случай падения рендера — пересоздать нельзя, просто лог
  });
}

app.whenReady().then(() => {
  Menu.setApplicationMenu(null);
  createWindow();
  startBridge();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  stopBridge();
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  stopBridge();
});

// IPC
ipcMain.handle("bridge:cmd", (_e, msg) => {
  sendToBridge(msg);
  return { queued: true };
});

ipcMain.handle("bridge:list", () => {
  sendToBridge({ cmd: "list" });
  return { queued: true };
});
