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
  // Only the first load starts from a clean slate; later loads test
  // reconnecting with the remembered device.
  if (localStorage.getItem("m4-test-keep-last") === "true") localStorage.removeItem("m4-test-keep-last");
  else localStorage.removeItem("m4-last-connected");
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
      // Fresh session: the last connected device must reconnect on startup.
      await window.webContents.executeJavaScript('localStorage.setItem("m4-test-keep-last", "true")');
      await window.loadFile(path.join(__dirname, "../dist/index.html"));
      await window.webContents.executeJavaScript(`(${runStartupTest.toString()})()`);
      // Fresh session without history: auto-discovery (empty address).
      await window.webContents.executeJavaScript('localStorage.removeItem("m4-last-connected")');
      await window.loadFile(path.join(__dirname, "../dist/index.html"));
      await window.webContents.executeJavaScript(`(${runAutoDiscoveryTest.toString()})()`);
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
  const connect = mock.requests.shift();
  assert.equal(connect.cmd, "connect", "startup must connect automatically");
  assert.equal(connect.addr, "test-device", "startup must reuse the last connected device");
  mock.reply(connect, { connected: true, addr: "test-device" });
  await flush();
  const get = mock.requests.shift();
  assert.equal(get.cmd, "get");
  mock.reply(get, { state: { anc: { enabled: true }, mode: { key: "custom", antiwind: 1 }, transparency: { level: 25 } } });
  await flush();
  assert.match(document.body.textContent, /Connected/);
  assert.equal(mock.requests.length, 0);
}

async function runAutoDiscoveryTest() {
  const assert = require("node:assert/strict");
  const mock = window.pollTest;
  const flush = () => new Promise((resolve) => setTimeout(resolve, 40));
  await flush();
  const connect = mock.requests.shift();
  assert.equal(connect.cmd, "connect", "startup must connect automatically");
  assert.equal(connect.addr, "", "without history the bridge must discover the headphones");
  mock.reply(connect, { connected: true, addr: "auto-device" });
  await flush();
  assert.equal(localStorage.getItem("m4-last-connected"), "auto-device", "the discovered address must be remembered");
  const get = mock.requests.shift();
  mock.reply(get, { state: { anc: { enabled: true }, mode: { key: "custom", antiwind: 1 }, transparency: { level: 25 } } });
  await flush();
  assert.match(document.body.textContent, /Connected/);
  assert.equal(mock.requests.length, 0);
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
  const connectOk = { connected: true, addr: "test-device" };

  // Startup auto-connect with no history: empty address = bridge-side
  // discovery. The reply reports the resolved device address.
  const startup = await next("connect");
  assert.equal(startup.addr, "");
  await reply(startup, connectOk);
  await reply(await next("get"), state());
  connected();
  assert.match(document.body.textContent, /Battery: 82%/);
  assert.match(document.body.textContent, /Firmware: 3.18.0/);

  // The device reports anti-wind 0: switching to Max must send a command
  // (selecting the already-active level is skipped as a no-op).
  mock.poll();
  await reply(await next("get"), state("custom", 25, 0));
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

  mock.poll();
  await reply(await next("get"), state("comfort", 40, 2));
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

  // A failed status read surfaces the error and schedules an automatic retry.
  mock.poll();
  const failed = await next("get");
  mock.reply(failed, undefined, "connection lost");
  await flush();
  assert.match(document.body.textContent, /Error: connection lost/);
  assert.equal(button("Adaptive").disabled, true, "poll errors must remain visible");
  assert.equal(mock.hasRetry(), true, "connection errors schedule an automatic retry");
  mock.retry();
  await reply(await next("connect"), connectOk);
  await reply(await next("get"), state());
  connected();

  // A bridge crash settles the pending request immediately.
  mock.poll();
  const crashed = await next("get");
  mock.event({ event: "stopped", error: "bridge crashed" });
  await flush();
  assert.match(document.body.textContent, /bridge crashed/);
  await reply(crashed, state());
  assert.equal(mock.requests.length, 0, "late reply after a crash must not trigger reads");
  mock.retry();
  await reply(await next("connect"), connectOk);
  await reply(await next("get"), state());
  connected();

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
  mock.poll();
  await reply(await next("get"), { state: { anc: null, mode: null, transparency: null } });
  assert.match(document.body.textContent, /device state unavailable/);
  assert.ok(document.querySelector("img").naturalWidth > 0);
  mock.retry();
  await reply(await next("connect"), connectOk);
  await reply(await next("get"), state());
  connected();

  // A device notification (ANC button on the headphones) must trigger a
  // debounced background state refresh instead of waiting for the poll.
  mock.event({ event: "notification", id: "0x1A84", payload: "01" });
  await flush();
  assert.equal(mock.requests.length, 0, "notification refresh is debounced");
  await new Promise((resolve) => setTimeout(resolve, 400));
  await reply(await next("get"), state());
  assert.equal(mock.requests.length, 0);

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

  // System suspend cancels in-flight work; resume reconnects automatically.
  mock.action({ action: "suspend" });
  await flush();
  mock.action({ action: "resume" });
  const resumed = await next("connect");
  assert.equal(resumed.addr, "test-device");
  await reply(resumed, connectOk);
  await reply(await next("get"), state());
  connected();

  mock.poll();
  mock.reply(await next("get"), undefined, "connection lost");
  await flush();
  mock.retry();
  await reply(await next("connect"), connectOk);
  await reply(await next("get"), state());
  mock.poll();
  mock.reply(await next("get"), undefined, "connection lost");
  await flush();
  for (let attempt = 0; attempt < 3; attempt++) {
    assert.equal(mock.hasRetry(), true);
    mock.retry();
    await reply(await next("connect"), connectOk);
    mock.reply(await next("get"), undefined, "no acknowledgement");
    await flush();
  }
  assert.deepEqual(mock.retryDelays.slice(-4, -1), [15000, 30000, 60000]);
  assert.equal(mock.retryDelays.at(-1), 60000, "retries continue capped at 60 seconds");
  assert.equal(mock.hasRetry(), true, "automatic retries never give up");
  assert.match(document.body.textContent, /no acknowledgement/);

  // Resume reconnects even while slow retries continue.
  mock.action({ action: "suspend" });
  await flush();
  mock.action({ action: "resume" });
  const revived = await next("connect");
  assert.equal(revived.addr, "test-device");
  await reply(revived, connectOk);
  await reply(await next("get"), state());
  connected();

  button("Diagnostics").click();
  await flush();
  button("Export diagnostics").click();
  await flush();
  assert.match(document.body.textContent, /Diagnostics saved/);
}
