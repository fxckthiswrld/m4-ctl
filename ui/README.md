# Momentum 4 Control UI

Electron + Vite + React-интерфейс для управления Sennheiser Momentum 4 через
Python-мост из корня репозитория.

Основная документация, список возможностей, архитектура и инструкция релиза:
[../README.md](../README.md).

## Команды

```bash
npm install       # установка зависимостей
npm run dev       # Vite + Electron в режиме разработки
npm run bridge:build # standalone m4-bridge.exe
npm run build     # production frontend в dist
npm run dist       # bridge + frontend + electron-builder
npm start         # Electron с уже собранным dist
```

Для запуска из `npm run dev` в корне репозитория должны быть установлены Python
3.10+ и `uv`. Для `npm run dist` PyInstaller собирает Python-мост в
`build/bridge/m4-bridge.exe`, после чего Electron упаковывает его в установщик.
Готовый Windows-релиз не требует Python или `uv`.
