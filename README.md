# Momentum 4 Control

Неофициальный контроллер для наушников Sennheiser Momentum 4. Проект умеет
управлять ANC, режимами Ambient Sound, прозрачностью и Anti-Wind через Classic
Bluetooth SPP (RFCOMM) и протокол GAIA3.

В проекте есть два интерфейса:

- CLI на Python для отдельных команд и диагностики.
- Desktop UI на Electron + React для постоянного управления устройством.

## Возможности

| Возможность | CLI | UI |
|---|---:|---:|
| Список сопряжённых Bluetooth-устройств | Да | Да |
| Подключение к Momentum 4 | На время команды | Да |
| Отключение от Momentum 4 | После завершения команды | Да |
| ANC on/off | Да | Да |
| Adaptive | Да | Да |
| Custom | Да | Да |
| Comfort | Да | Нет отдельной кнопки |
| Anti-Wind: Off/Max/Auto | Да | Да |
| Прозрачность 0..100 | Да | Да, слайдер |
| Чтение состояния | Да | Да, после подключения |
| Keepalive и переподключение SPP | Для команд | Да |

## Требования

- Windows 10/11 или macOS.
- Python 3.10 или новее.
- [uv](https://docs.astral.sh/uv/) в `PATH`.
- Сопряжённые с компьютером Momentum 4 и включённый Bluetooth.
- Node.js 18+ и npm для разработки и сборки UI.

Python и `uv` нужны для CLI, разработки и сборки. Готовый Windows standalone
релиз содержит Python-мост внутри приложения и не требует Python или `uv` у
конечного пользователя.

На Linux транспорт пока не реализован.

## Установка

```bash
git clone https://github.com/fxckthiswrld/m4-ctl.git
cd m4-ctl
uv sync
```

Для UI установите npm-зависимости:

```bash
cd ui
npm install
cd ..
```

## CLI

Сначала выведите список сопряжённых устройств и найдите адрес Momentum 4:

```bash
uv run python m4_ctl.py list
```

Пример:

```text
AA:BB:CC:DD:EE:FF  MOMENTUM 4
```

Все команды, кроме `list`, используют `--addr`:

```bash
uv run python m4_ctl.py --addr AA:BB:CC:DD:EE:FF anc on
uv run python m4_ctl.py --addr AA:BB:CC:DD:EE:FF anc off
uv run python m4_ctl.py --addr AA:BB:CC:DD:EE:FF mode adaptive
uv run python m4_ctl.py --addr AA:BB:CC:DD:EE:FF mode comfort
uv run python m4_ctl.py --addr AA:BB:CC:DD:EE:FF mode anti_wind
uv run python m4_ctl.py --addr AA:BB:CC:DD:EE:FF mode off
uv run python m4_ctl.py --addr AA:BB:CC:DD:EE:FF custom
uv run python m4_ctl.py --addr AA:BB:CC:DD:EE:FF transparency 50
uv run python m4_ctl.py --addr AA:BB:CC:DD:EE:FF antiwind 2
uv run python m4_ctl.py --addr AA:BB:CC:DD:EE:FF get
```

Значения:

- `transparency 0` - максимальный ANC в Custom, `100` - максимальная прозрачность.
- `antiwind 0` - Off, `1` - Max, `2` - Auto.
- Для прозрачности мост сначала выходит из Adaptive, затем отправляет значение
  слайдера.

## Desktop UI

В режиме разработки из корня репозитория:

```bash
cd ui
npm run dev
```

Команда запускает Vite на `http://localhost:5173` и Electron. Electron
автоматически запускает Python-мост из корня репозитория.

Сборка frontend без установщика:

```bash
cd ui
npm run build
```

Сборка Electron-пакета и установщика:

```bash
cd ui
npm run dist
```

Артефакты создаются electron-builder в `ui/release`. Собирать установщик нужно
на целевой ОС: Windows-сборка не
заменяет macOS-сборку и наоборот.

В режиме разработки Electron запускает `uv run python bridge.py`. В packaged
режиме он запускает встроенный `m4-bridge.exe`.

## Архитектура

```text
React renderer
    | contextBridge + IPC
Electron main process
    | JSON Lines по stdin/stdout
bridge.py
    | GAIA3 over SPP/RFCOMM
Momentum 4
```

`gaia_transport.py` выбирает транспорт по ОС:

- Windows: WinRT `StreamSocket` и RFCOMM.
- macOS: PyObjC `IOBluetooth` и RFCOMM.

`bridge.py` держит транспорт открытым в течение работы UI, отвечает на JSON Lines
команды и восстанавливает соединение, если наушники закрыли SPP-канал после
ответа.

Команды bridge-протокола: `list`, `connect`, `anc`, `mode`, `custom`, `antiwind`,
`transparency`, `get`, `close`.

Основные файлы:

```text
m4_ctl.py             CLI и команды GAIA3
bridge.py             постоянный JSON Lines-мост для Electron
gaia_transport.py     SPP/RFCOMM транспорт Windows и macOS
pyproject.toml        Python-зависимости
uv.lock               зафиксированные Python-зависимости
ui/src/App.tsx        интерфейс управления
ui/electron/main.cjs  Electron main process и запуск bridge.py
ui/package.json       npm-команды и electron-builder
```

## Протокол

Momentum 4 управляется не по BLE, а по Classic Bluetooth SPP. Используется GAIA3
с vendor `0x0495` и сервисом
`a2129ff3-081b-4c45-8afe-469d9c4842ec`.

Полезные команды GAIA:

| Команда | Назначение |
|---|---|
| `0x1A00 [mode, state]` | Режим ANC: `1` Anti-Wind, `2` Comfort, `3` Adaptive |
| `0x1A02 [level]` | Слайдер Custom, `0..100` |
| `0x1A04 [0/1]` | ANC off/on |
| `0x1804 [0/1]` | TransparentHearing off/on |
| `0x1A05`, `0x1A01`, `0x1A03`, `0x1805` | Чтение состояния |

Протокол восстановлен анализом JS-бандла официального приложения Sennheiser
Smart Control. Поведение может отличаться между версиями прошивки.

## Проверка перед публикацией

```bash
uv sync
uv run python m4_ctl.py --help
uv run python bridge.py
```

В отдельном окне UI:

```bash
cd ui
npm ci
npm run build
```

Для проверки реального управления подключите наушники и выполните `list`,
`connect` через UI или команды CLI. `bridge.py` завершайте через `Ctrl+C`.

## Standalone-релиз Windows

Полная сборка Windows автоматически выполняет два шага: PyInstaller собирает
`bridge.py` в `build/bridge/m4-bridge.exe`, затем electron-builder добавляет этот
файл в приложение.

```powershell
cd ui
npm ci
npm run dist
```

Результаты находятся в `ui/release`: NSIS-установщик и portable `.exe`. Для
проверки portable-версии используйте компьютер без Python и `uv`.

## Push и релиз

Ниже предполагается, что remote `origin` уже указывает на GitHub-репозиторий.

1. Проверьте изменения и состояние рабочей копии:

   ```bash
   git status
   git diff --check
   ```

2. Обновите версии перед релизом. Версию приложения Electron меняйте в
   `ui/package.json`; версию Python-пакета - в `pyproject.toml`, если она должна
   совпадать. Например, замените `0.1.0` на `0.2.0`.

3. Выполните проверки сборки:

   ```bash
   uv sync
   cd ui
   npm ci
   npm run build
   cd ..
   ```

4. Зафиксируйте только нужные файлы:

   ```bash
   git add README.md ui/README.md ui/package.json ui/electron/main.cjs pyproject.toml uv.lock .gitignore
    git commit -m "build: add standalone Windows release"
   ```

   Если версии не менялись, уберите соответствующие файлы из `git add`. Не
   добавляйте `ui/dist`, `ui/release`, `node_modules`, APK и временные тестовые
   файлы без отдельного решения.

5. Отправьте ветку и тег:

   ```bash
   git push origin master
   git tag -a v0.2.0 -m "Release v0.2.0"
   git push origin v0.2.0
   ```

6. Соберите Windows-установщик на этой Windows-машине:

   ```powershell
   cd ui
   npm ci
   npm run dist
   ```

   Проверьте полученный установщик на чистой машине без Python и `uv`.

7. Создайте GitHub Release для тега `v0.2.0` на странице Releases и прикрепите
   собранные файлы из `ui/release`. В описании укажите изменения, поддерживаемые
   ОС и известные ограничения.

При наличии GitHub CLI шаг 7 можно выполнить так:

```bash
gh release create v0.2.0 ui/release/* --title "v0.2.0" --generate-notes
```

## Ограничения и предупреждение

- Проект неофициальный и не связан с Sennheiser.
- Для Classic Bluetooth SPP требуется предварительное сопряжение наушников с
  системой.
- Реализация проверялась на ограниченном наборе устройств и версий прошивки.
- Используйте проект на свой страх и риск.

## License

MIT
