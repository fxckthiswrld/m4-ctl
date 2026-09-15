const { app, BrowserWindow } = require("electron");
const path = require("node:path");

if (process.type === "renderer") {
  const requests = [];
  let onReply;
  let onEvent;
  let onAction;
  let desktopState;
  let retry;
  const retryDelays = [];
  let poll;
  const originalSetInterval = window.setInterval.bind(window);
  const originalSetTimeout = window.setTimeout.bind(window);
  const originalClearTimeout = window.clearTimeout.bind(window);
  window.setTimeout = (callback, delay, ...args) => {
    const timer = originalSetTimeout(callback, delay, ...args);
    if ([15000, 30000, 60000].includes(delay)) {
      retry = { callback, timer };
      retryDelays.push(delay);
    }
    return timer;
  };
  window.clearTimeout = (timer) => {
    if (retry?.timer === timer) retry = undefined;
    originalClearTimeout(timer);
  };
  window.setInterval = (callback, delay, ...args) => {
    if (delay === 10000) poll = callback;
    return originalSetInterval(callback, delay, ...args);
  };
  localStorage.setItem("m4-language", "en");
  localStorage.setItem("m4-device-address", "test-device");
  if (localStorage.getItem("m4-test-startup") !== "true") localStorage.removeItem("m4-auto-connect");
  localStorage.removeItem("m4-test-startup");
  localStorage.removeItem("m4-close-to-tray");
  localStorage.removeItem("m4-profiles");
  window.desktop = {
    sync: async (state) => { desktopState = state; },
    onAction: (callback) => { onAction = callback; return () => {}; },
    exportDiagnostics: async () => ({ saved: true }),
  };
  window.m4 = {
    cmd: async (request) => {
      if (request.cmd === "info") { onReply({ id: request.id, ok: true, result: { battery: [82], firmware: "3.18.0" } }); return; }
      requests.push(request);
    },
    onReply: (callback) => { onReply = callback; return () => {}; },
    onLog: () => () => {},
    onEvent: (callback) => { onEvent = callback; return () => {}; },
    status: async () => ({ event: "ready" }),
    cancel: async () => onEvent({ event: "stopped", cancelled: true }),
  };
  window.pollTest = {
    requests,
    poll: () => poll(),
    event: (event) => onEvent(event),
    action: (event) => onAction(event),
    desktop: () => desktopState,
    retryDelays,
    hasRetry: () => !!retry,
    retry: () => {
      const pending = retry;
      if (pending) {
        window.clearTimeout(pending.timer);
        pending.callback();
      }
    },
    reply: (request, result, error) => onReply({
      id: request.id, ok: !error, result, error,
    }),
  };
} else {
  app.whenReady().then(async () => {
    const window = new BrowserWindow({
      show: false,
      width: 420,
      height: 860,
      webPreferences: {
        preload: __filename,
        contextIsolation: false,
        nodeIntegration: true,
        sandbox: false,
      },
    });
    try {
      await window.loadFile(path.join(__dirname, "../dist/index.html"));
      await window.webContents.executeJavaScript(`(${runTests.toString()})()`);
      await window.webContents.executeJavaScript('localStorage.setItem("m4-auto-connect", "true"); localStorage.setItem("m4-test-startup", "true")');
      await window.loadFile(path.join(__dirname, "../dist/index.html"));
      await window.webContents.executeJavaScript(`(${runStartupTest.toString()})()`);
      await window.webContents.executeJavaScript('localStorage.setItem("m4-test-startup", "true")');
      await window.loadFile(path.join(__dirname, "../dist/index.html"));
      await window.webContents.executeJavaScript(`(${runManualStopReloadTest.toString()})()`);
      console.log("Status polling UI regression tests passed");
      app.exit(0);
    } catch (error) {
      console.error(error);
      app.exit(1);
    }
  });
}

async function runStartupTest() {
  const assert = require("node:assert/strict");
  const mock = window.pollTest;
  const flush = () => new Promise((resolve) => setTimeout(resolve, 40));
  await flush();
  const list = mock.requests.shift();
  assert.equal(list.cmd, "list");
  mock.reply(list, [{ name: "MOMENTUM 4", address: "test-device" }]);
  await flush();
  const connect = mock.requests.shift();
  assert.equal(connect.cmd, "connect", "startup must reconnect the last successful device");
  assert.equal(connect.addr, "test-device");
  document.querySelectorAll("button").forEach((button) => { if (button.textContent.trim() === "Cancel") button.click(); });
  await flush();
  assert.equal(mock.requests.length, 0);
}

async function runManualStopReloadTest() {
  const assert = require("node:assert/strict");
  const mock = window.pollTest;
  const flush = () => new Promise((resolve) => setTimeout(resolve, 40));
  await flush();
  const list = mock.requests.shift();
  assert.equal(list.cmd, "list");
  mock.reply(list, []);
  await flush();
  mock.action({ action: "resume" });
  await flush();
  assert.equal(mock.requests.length, 0, "manual cancel must survive renderer recovery");
  assert.equal(localStorage.getItem("m4-auto-connect"), "true");
}

async function runTests() {
  const assert = require("node:assert/strict");
  const mock = window.pollTest;
  const flush = () => new Promise((resolve) => setTimeout(resolve, 40));
  const button = (label) => [...document.querySelectorAll("button")]
    .find((element) => element.textContent.trim() === label);
  const state = (mode = "custom", level = 25, antiwind = 1) => ({
    state: { anc: { enabled: true }, mode: { key: mode, antiwind }, transparency: { level } },
  });
  const next = async (command) => {
    await flush();
    const request = mock.requests.shift();
    assert.equal(request?.cmd, command);
    return request;
  };
  const reply = async (request, result) => {
    mock.reply(request, result);
    await flush();
  };
  const connected = () => assert.match(document.body.textContent, /Connected/);

  await reply(await next("list"), []);
  assert.match(document.body.textContent, /No paired Bluetooth devices found/);
  const deviceSelect = document.querySelector('select[aria-label="Device"]');
  assert.equal(deviceSelect.value, "test-device");
  assert.match(deviceSelect.selectedOptions[0].textContent, /Saved device/);
  document.querySelector('[aria-label="Refresh device list"]').click();
  const failedList = await next("list");
  mock.reply(failedList, undefined, "Bluetooth disabled");
  await flush();
  assert.match(document.querySelector('[role="alert"]').textContent, /Device search failed: Bluetooth disabled/);
  assert.equal(deviceSelect.value, "test-device");
  document.querySelector('[aria-label="Refresh device list"]').click();
  await reply(await next("list"), [{ name: "MOMENTUM 4", address: "test-device" }]);
  assert.equal(document.querySelector('[role="alert"]'), null);
  assert.match(deviceSelect.selectedOptions[0].textContent, /MOMENTUM 4/);
  button("Connect").click();
  await reply(await next("connect"), {});
  await reply(await next("get"), state());
  connected();

  button("Max").click();
  await reply(await next("antiwind"), {});
  await reply(await next("get"), state());
  const selectedAntiwindClass = button("Max").className;
  const snapshot = document.body.innerHTML;
  for (let i = 0; i < 3; i++) {
    mock.poll();
    const request = await next("get");
    assert.equal(button("Adaptive").disabled, false, "poll must leave controls enabled");
    assert.equal(document.body.innerHTML, snapshot, "pending poll must not alter the UI");
    mock.poll();
    assert.equal(mock.requests.length, 0, "slow polls must not overlap");
    await reply(request, state());
    assert.equal(document.body.innerHTML, snapshot, "unchanged status must not alter the UI");
    assert.equal(button("Max").className, selectedAntiwindClass);
  }

  mock.poll();
  await reply(await next("get"), state("adaptive", 40));
  assert.match(document.querySelector('[aria-live="polite"]').textContent, /Adaptive.*40%/);

  mock.poll();
  await reply(await next("get"), state("comfort", 40, 2));
  assert.match(document.querySelector('[aria-live="polite"]').textContent, /Comfort/);
  assert.equal(button("Adaptive").getAttribute("aria-pressed"), "false");
  assert.equal(button("Custom").getAttribute("aria-pressed"), "false");
  assert.equal(button("Auto").getAttribute("aria-pressed"), "true");
  mock.poll();
  await reply(await next("get"), state("adaptive", 40));

  mock.poll();
  const stale = await next("get");
  button("Custom").click();
  const custom = await next("custom");
  assert.equal(button("Adaptive").disabled, true, "user command must still show busy state");
  await reply(stale, state("adaptive", 5));
  assert.equal(button("Adaptive").disabled, true, "poll completion must not clear command busy state");
  assert.match(button("Custom").lastElementChild.className, /text-primary/);
  mock.poll();
  assert.equal(mock.requests.length, 0, "poll must skip outstanding user commands");
  await reply(custom, {});
  await reply(await next("get"), state());

  mock.poll();
  const beforeDrag = await next("get");
  const slider = document.querySelector(".touch-none");
  // Synthetic pointer events have no active browser pointer capture.
  slider.setPointerCapture = () => {};
  slider.releasePointerCapture = () => {};
  const rect = slider.getBoundingClientRect();
  const pointer = (type) => slider.dispatchEvent(new PointerEvent(type, {
    bubbles: true, pointerId: 1, clientX: rect.left + rect.width * 0.75,
  }));
  pointer("pointerdown");
  await flush();
  const dragging = slider.innerHTML;
  await reply(beforeDrag, state("custom", 5));
  assert.equal(slider.innerHTML, dragging, "old status must not move a dragged slider");
  mock.poll();
  assert.equal(mock.requests.length, 0, "poll must pause during a slider drag");
  pointer("pointerup");
  await reply(await next("transparency"), {});
  await reply(await next("get"), state("custom", 75));

  assert.equal(slider.getAttribute("role"), "slider");
  assert.ok(slider.getAttribute("aria-label"));
  assert.equal(slider.tabIndex, 0);
  const key = (type, key) => slider.dispatchEvent(new KeyboardEvent(type, { bubbles: true, cancelable: true, key }));
  mock.poll();
  const beforeKeyboard = await next("get");
  key("keydown", "ArrowRight");
  key("keydown", "ArrowRight");
  await flush();
  assert.equal(slider.getAttribute("aria-valuenow"), "85", "repeated keys must accumulate before React renders");
  await reply(beforeKeyboard, state("custom", 5));
  assert.equal(slider.getAttribute("aria-valuenow"), "85", "poll must not overwrite keyboard input");
  mock.poll();
  assert.equal(mock.requests.length, 0);
  key("keyup", "ArrowRight");
  const keyboardCommit = await next("transparency");
  assert.equal(keyboardCommit.level, 85);
  assert.equal(slider.getAttribute("aria-disabled"), "true");
  key("keydown", "Home");
  key("keyup", "Home");
  assert.equal(mock.requests.length, 0, "disabled slider must ignore keyboard input");
  await reply(keyboardCommit, {});
  await reply(await next("get"), state("custom", 85));
  for (const [direction, level] of [["Home", 0], ["End", 100]]) {
    key("keydown", direction);
    key("keyup", direction);
    const command = await next("transparency");
    assert.equal(command.level, level);
    await reply(command, {});
    await reply(await next("get"), state("custom", level));
  }
  key("keydown", "ArrowRight");
  key("keyup", "ArrowRight");
  assert.equal(mock.requests.length, 0, "upper boundary must not send redundant commands");
  slider.focus();
  key("keydown", "ArrowLeft");
  slider.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
  const blurCommit = await next("transparency");
  assert.equal(blurCommit.level, 95, "leaving the slider must commit pending keyboard input");
  await reply(blurCommit, {});
  await reply(await next("get"), state("custom", 95));
  pointer("pointerdown");
  pointer("pointercancel");
  mock.poll();
  await reply(await next("get"), state("custom", 95));
  assert.equal(slider.getAttribute("aria-valuenow"), "95", "cancelled drag must allow polling to resume");

  mock.poll();
  const beforeDisconnect = await next("get");
  button("Disconnect").click();
  const close = await next("close");
  await reply(beforeDisconnect, state("adaptive", 90));
  assert.match(document.body.textContent, /Not connected/);
  assert.match(document.querySelector('[aria-live="polite"]').textContent, /Unknown/);
  await reply(close, {});

  button("Connect").click();
  await reply(await next("connect"), {});
  await reply(await next("get"), state());
  mock.poll();
  const failed = await next("get");
  mock.reply(failed, undefined, "connection lost");
  await flush();
  assert.match(document.body.textContent, /Error: connection lost/);
  assert.equal(button("Adaptive").disabled, true, "poll errors must remain visible");

  button("Connect").click();
  const cancelled = await next("connect");
  button("Cancel").click();
  await flush();
  await reply(cancelled, {});
  assert.match(document.body.textContent, /Not connected/);
  assert.equal(mock.requests.length, 0, "late connect reply must not trigger status reads");
  assert.equal(button("Connect").disabled, false);

  button("Connect").click();
  const crashed = await next("connect");
  mock.event({ event: "stopped", error: "bridge crashed" });
  await flush();
  assert.match(document.body.textContent, /bridge crashed/);
  assert.equal(button("Connect").disabled, false, "crash must settle pending requests immediately");
  await reply(crashed, {});
  assert.equal(mock.requests.length, 0);

  mock.event({ event: "ready" });
  button("Connect").click();
  await reply(await next("connect"), {});
  await reply(await next("get"), state());
  mock.poll();
  const recovering = await next("get");
  mock.event({ event: "reconnecting" });
  await flush();
  assert.match(document.body.textContent, /Reconnecting/);
  assert.equal(button("Adaptive").disabled, true);
  mock.event({ event: "operation-complete" });
  await reply(recovering, { state: { anc: { enabled: true }, mode: null, transparency: null } });
  assert.equal(button("Max").getAttribute("aria-pressed"), "false");
  assert.equal(button("Auto").getAttribute("aria-pressed"), "false");
  assert.match(document.querySelector('[aria-live="polite"]').textContent, /Unknown/);
  mock.poll();
  await reply(await next("get"), { state: { anc: null, mode: null, transparency: null } });
  assert.match(document.body.textContent, /device state unavailable/);
  assert.ok(document.querySelector("img").naturalWidth > 0);

  button("Connect").click();
  await reply(await next("connect"), {});
  await reply(await next("get"), state());
  assert.match(document.body.textContent, /Battery: 82%/);
  assert.match(document.body.textContent, /Firmware: 3.18.0/);

  const input = document.querySelector('input[aria-label="Profile name"]');
  const enterName = async (value) => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    await flush();
  };
  await enterName("Quiet office");
  document.querySelector('[aria-label="Save current settings"]').click();
  await flush();
  let profiles = JSON.parse(localStorage.getItem("m4-profiles"));
  const saved = profiles.find((p) => p.name === "Quiet office");
  assert.equal(saved.mode, "custom");
  assert.equal(saved.transparency, 25);
  assert.ok(mock.desktop().profiles.some((p) => p.id === saved.id));
  mock.action({ action: "profile", id: saved.id });
  const profileRequest = await next("profile");
  assert.equal(profileRequest.antiwind, 1);
  await reply(profileRequest, state());
  await enterName("Home office");
  document.querySelector('[aria-label="Rename profile"]').click();
  await flush();
  assert.ok(JSON.parse(localStorage.getItem("m4-profiles")).some((p) => p.name === "Home office"));
  document.querySelector('[aria-label="Delete profile"]').click();
  await flush();
  assert.ok(!JSON.parse(localStorage.getItem("m4-profiles")).some((p) => p.id === saved.id));

  mock.action({ action: "mode", mode: "adaptive" });
  const trayMode = await next("mode");
  mock.action({ action: "mode", mode: "off" });
  await flush();
  assert.equal(mock.requests.length, 0, "tray must not overlap foreground operations");
  await reply(trayMode, {});
  await reply(await next("get"), state("adaptive"));

  button("Application").click();
  await flush();
  document.querySelector('[aria-label="Close to tray"]').click();
  await flush();
  assert.equal(mock.desktop().closeToTray, true);
  document.querySelector('[aria-label="Automatically connect"]').click();
  await flush();
  assert.equal(localStorage.getItem("m4-auto-connect"), "true");
  mock.action({ action: "suspend" });
  await flush();
  mock.action({ action: "resume" });
  await reply(await next("connect"), {});
  await reply(await next("get"), state());
  mock.poll();
  mock.reply(await next("get"), undefined, "connection lost");
  await flush();
  mock.retry();
  await reply(await next("connect"), {});
  await reply(await next("get"), state());
  mock.poll();
  mock.reply(await next("get"), undefined, "connection lost");
  await flush();
  for (let attempt = 0; attempt < 3; attempt++) {
    assert.equal(mock.hasRetry(), true);
    mock.retry();
    await reply(await next("connect"), {});
    mock.reply(await next("get"), undefined, "no acknowledgement");
    await flush();
  }
  assert.deepEqual(mock.retryDelays.slice(-3), [15000, 30000, 60000]);
  assert.equal(mock.hasRetry(), false, "failed state reads must not reset the retry budget");
  assert.match(document.body.textContent, /no acknowledgement/);
  mock.action({ action: "connect" });
  await reply(await next("connect"), {});
  await reply(await next("get"), state());
  mock.action({ action: "disconnect" });
  await reply(await next("close"), {});
  mock.action({ action: "suspend" });
  await flush();
  mock.action({ action: "resume" });
  mock.retry();
  await flush();
  assert.equal(mock.requests.length, 0, "manual disconnect must suppress automatic reconnect");

  button("Diagnostics").click();
  await flush();
  button("Export diagnostics").click();
  await flush();
  assert.match(document.body.textContent, /Diagnostics saved/);
  // Model a fresh application session before testing startup reconnect.
  sessionStorage.removeItem("m4-manually-stopped");
}
