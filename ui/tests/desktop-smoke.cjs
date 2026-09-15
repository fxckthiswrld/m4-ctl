const { app, BrowserWindow } = require("electron");
const assert = require("node:assert/strict");
const { once } = require("node:events");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const userData = process.env.M4_SMOKE_USER_DATA || fs.mkdtempSync(path.join(os.tmpdir(), "m4-desktop-smoke-"));
app.setPath("userData", userData);
app.on("browser-window-created", (_event, window) => window.hide());
try {
  if (process.env.M4_SMOKE_RESOURCES) {
    Object.defineProperty(process, "resourcesPath", { value: path.resolve(process.env.M4_SMOKE_RESOURCES) });
    Object.defineProperty(app, "isPackaged", { value: true });
    require(path.join(process.resourcesPath, "app.asar", "electron", "main.cjs"));
  } else require("../electron/main.cjs");
} catch (error) {
  console.error(error);
  app.exit(1);
}

if (!process.env.M4_SMOKE_SECONDARY) {
  const timeout = setTimeout(() => {
    console.error("Desktop smoke timed out");
    process.exitCode = 1;
    app.quit();
  }, 30000);

  app.whenReady().then(async () => {
    try {
      const window = BrowserWindow.getAllWindows()[0];
      await once(window.webContents, "did-finish-load");
      await verifyBridge(window);

      const secondInstance = once(app, "second-instance");
      const args = [...(process.argv.includes("--no-sandbox") ? ["--no-sandbox"] : []), __filename];
      const child = spawn(process.execPath, args, {
        env: { ...process.env, M4_SMOKE_USER_DATA: userData, M4_SMOKE_SECONDARY: "1" },
        windowsHide: true,
        stdio: "ignore",
      });
      const exited = once(child, "exit");
      await secondInstance;
      assert.equal((await exited)[0], 0);
      assert.equal(BrowserWindow.getAllWindows().length, 1);
      assert.equal(window.isVisible(), true);
      window.hide();

      await window.webContents.executeJavaScript('sessionStorage.setItem("m4-manually-stopped", "true")');
      const reloaded = once(window.webContents, "did-finish-load");
      window.webContents.forcefullyCrashRenderer();
      await reloaded;
      await verifyBridge(window);
      assert.equal(await window.webContents.executeJavaScript('sessionStorage.getItem("m4-manually-stopped")'), "true");
      await window.webContents.executeJavaScript('window.desktop.sync({ language: "en", connected: false, busy: false, canConnect: false, closeToTray: true, mode: null, profiles: [] })');
      window.close();
      assert.equal(window.isDestroyed(), false);
      assert.equal(window.isVisible(), false);
      console.log("Desktop smoke passed: real bridge, single instance, renderer crash recovery, close to tray");
    } catch (error) {
      console.error(error);
      process.exitCode = 1;
    } finally {
      clearTimeout(timeout);
      app.quit();
    }
  });
}

async function verifyBridge(window) {
  const result = await window.webContents.executeJavaScript(`(async () => {
    const reply = await new Promise((resolve, reject) => {
      const id = crypto.randomUUID();
      const off = window.m4.onReply((message) => {
        if (message.id !== id) return;
        off(); resolve(message);
      });
      window.m4.cmd({ id, cmd: "list" }).catch(reject);
    });
    return { reply, status: await window.m4.status(), imageLoaded: document.querySelector("img").naturalWidth > 0 };
  })()`);
  assert.equal(result.reply.ok, true);
  assert.equal(result.status.event, "ready");
  assert.equal(result.imageLoaded, true);
}
