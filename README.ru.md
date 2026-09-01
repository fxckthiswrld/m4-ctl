# Momentum 4 Control

Неофициальное приложение для управления Sennheiser Momentum 4 по Classic
Bluetooth SPP (RFCOMM) и протоколу GAIA3.

Поддерживаются Windows и macOS.

## Возможности

- Поиск сопряжённых Bluetooth-устройств и подключение к Momentum 4.
- ANC: Adaptive, Custom, Comfort и Off.
- Anti-Wind: Off, Max и Auto.
- Настройка прозрачности в Custom.
- Desktop-приложение Electron с Python-мостом Bluetooth.

## Требования для разработки

- Python 3.10+ и [uv](https://docs.astral.sh/uv/).
- Node.js 18+ и npm для Electron UI.
- Сопряжённые с компьютером Momentum 4.

Готовые standalone-релизы уже содержат Python-мост. Пользователям Windows и
macOS не нужны Python и `uv`.

## Быстрый старт

Установите зависимости Python-моста и запустите desktop-приложение:

```bash
uv sync
cd ui
npm ci
npm run dev
```

## Desktop UI

```bash
cd ui
npm ci
npm run dev
```

Electron запускает `bridge.py` автоматически. В окне приложения выберите
наушники, подключитесь и настройте режимы.

## Standalone-сборки

Сборка должна выполняться на целевой ОС: PyInstaller не собирает macOS-бинарник
на Windows и наоборот.

### Windows

```powershell
cd ui
npm ci
npm run dist:win
```

Артефакты: `ui/release/*.exe`.

GitHub Actions запускает тесты моста и проверки UI для каждого push и pull
request. При отправке тега вида `v*` дополнительно собираются native-артефакты
для Windows и macOS (`arm64`, `x64` и universal) и публикуются в GitHub Release.

### macOS

На Mac установите `uv` и Node.js, затем:

```bash
uv sync
cd ui
npm ci
npm run dist:mac
```

Артефакты: `ui/release/*.dmg` и `ui/release/*.zip`.

Для Apple Silicon собирайте на Apple Silicon Mac, для Intel - на Intel Mac.
macOS может показать предупреждение при запуске, пока приложение не подписано и
не notarized Apple.

## Технически

Momentum 4 использует GAIA3 с vendor `0x0495` и RFCOMM-сервисом
`a2129ff3-081b-4c45-8afe-469d9c4842ec`.

- `bridge.py` - JSON Lines-мост для Electron.
- `gaia_transport.py` - SPP-транспорт: WinRT на Windows и IOBluetooth на macOS.

## Предупреждение

Проект не связан с Sennheiser. Протокол восстановлен эмпирически и может
отличаться между версиями прошивки. Используйте на свой страх и риск.

## License

MIT
