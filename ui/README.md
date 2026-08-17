# Momentum 4 Control UI

Electron + Vite + React-интерфейс для управления Sennheiser Momentum 4 через
Python-мост из корня репозитория.

Основная документация, список возможностей, архитектура и инструкция релиза:
[../README.md](../README.md).

## Команды

```bash
npm install       # установка зависимостей
npm run dev       # Vite + Electron в режиме разработки
npm run bridge:build:win # standalone Windows bridge
npm run bridge:build:mac # standalone macOS bridge, only on macOS
npm run build            # production frontend in dist
npm run dist:win         # Windows installer + portable exe
npm run dist:mac         # macOS dmg + zip
npm start                # Electron with already built dist
```

Для `npm run dev` нужны Python 3.10+ и `uv`. Windows bridge собирается в
`build/bridge/win/m4-bridge.exe`, macOS bridge - в
`build/bridge/mac/m4-bridge`. Готовые Windows и macOS-релизы не требуют Python
или `uv`; macOS-сборку нужно выполнять на Mac.
