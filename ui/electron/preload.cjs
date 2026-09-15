const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("desktop", {
  sync: (state) => ipcRenderer.invoke("desktop:sync", state),
  exportDiagnostics: (snapshot) => ipcRenderer.invoke("desktop:export", snapshot),
  onAction: (callback) => {
    const listener = (_event, action) => callback(action);
    ipcRenderer.on("desktop:action", listener);
    return () => ipcRenderer.removeListener("desktop:action", listener);
  },
});

contextBridge.exposeInMainWorld("m4", {
  // Отправить команду в Python-мост. Ответ придёт через onReply.
  cmd: (msg) => ipcRenderer.invoke("bridge:cmd", msg),
  status: () => ipcRenderer.invoke("bridge:status"),
  cancel: () => ipcRenderer.invoke("bridge:cancel"),
  onEvent: (cb) => {
    const listener = (_e, event) => cb(event);
    ipcRenderer.on("bridge:event", listener);
    return () => ipcRenderer.removeListener("bridge:event", listener);
  },
  // Подписка на JSON-ответы моста; возвращает функцию отписки.
  onReply: (cb) => {
    const listener = (_e, msg) => cb(msg);
    ipcRenderer.on("bridge:reply", listener);
    return () => ipcRenderer.removeListener("bridge:reply", listener);
  },
  // Подписка на stderr/лог моста; возвращает функцию отписки.
  onLog: (cb) => {
    const listener = (_e, text) => cb(text);
    ipcRenderer.on("bridge:log", listener);
    return () => ipcRenderer.removeListener("bridge:log", listener);
  },
});
