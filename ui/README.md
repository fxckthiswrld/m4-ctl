# Momentum 4 Control (UI)

Electron + Vite + React + Tailwind (shadcn/ui) оболочка для управления
Sennheiser Momentum 4 через Python-мост (`bridge.py`).

## Запуск (dev)

```bash
cd ui
npm install
npm run dev
```

Electron откроет окно; Vite dev server на `http://localhost:5173`.
Мост (`uv run python bridge.py`) стартует автоматически из корня репозитория.

## Сборка

```bash
npm run build        # статический бандл в ui/dist
npm run dist         # сборка + electron-builder (установщик)
```

## Как это работает

```
React (renderer) --IPC--> main.cjs --stdin/stdout JSON lines--> bridge.py --GAIA3 SPP--> M4
```

- `bridge.py` держит SPP-соединение открытым (keepalive) всё время работы аппы;
  если наушник закрыл канал после ответа, мост пересоздаёт транспорт.
- Протокол моста: JSON lines. Вход `{"cmd": "..."}`, выход `{"ok": true/false, ...}`.
- Команды: `list`, `connect`, `anc`, `mode`, `custom`, `antiwind`, `transparency`, `get`, `close`.

## Команды M4 (реализованные)

| UI            | GAIA                             |
| ------------- | -------------------------------- |
| Adaptive      | `0x1A00 [3,1]`                   |
| Custom        | `0x1804[0]` -> `0x1A04[1]` -> `0x1A00[3,0]` -> `0x1A02[0]` |
| Off (ANC)     | `0x1A04 [0]`                     |
| Anti-Wind     | `0x1A00 [1, level]` (0/1/2)      |
| Transparency  | `0x1A00[3,0]` + `0x1A02[level]`  |
