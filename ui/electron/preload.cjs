const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("m4", {
  // Отправить команду в Python-мост. Ответ придёт через onReply.
  cmd: (msg) => ipcRenderer.invoke("bridge:cmd", msg),
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
