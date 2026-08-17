const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("m4", {
  // Отправить команду в Python-мост. Ответ придёт через onReply.
  cmd: (msg) => ipcRenderer.invoke("bridge:cmd", msg),
  // Подписка на JSON-ответы моста: (msg) => void
  onReply: (cb) => ipcRenderer.on("bridge:reply", (_e, msg) => cb(msg)),
  // Подписка на stderr/лог моста: (text) => void
  onLog: (cb) => ipcRenderer.on("bridge:log", (_e, text) => cb(text)),
});